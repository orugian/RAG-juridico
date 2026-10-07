"""P2A local staging only. No chunks, embeddings, index publication or approvals.

CLI inputs always pass load_curated. All file paths/hashes are verified before
parsing, and each file gets a record. Worker processes are serial and time bounded.
Human fidelity/provenance/relation qualification remains a separate P2B gate.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from importlib import metadata as package_metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from uuid import uuid4


def _hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_reviewed_generation(*, manager, generation_id, bundle, source_paths,
                              source_documents=None, conversion_paths=None, reuse_from=None, fault=None):
    """Separate P4 batch entrypoint for an explicitly reviewed canonical bundle.

    This does not convert staging success into approval, mutate quarantine or
    import decisions. Callers obtain units/chunks via the governed P3B builders.
    The existing staging CLI remains staging-only, with no publication default.
    """
    from app.retrieval.indexer import build_governed_generation
    return build_governed_generation(manager=manager, generation_id=generation_id, bundle=bundle,
        source_paths=source_paths, source_documents=source_documents, conversion_paths=conversion_paths,
        reuse_from=reuse_from, fault=fault)


def _installed_packages():
    """Actual installed versions, in addition to the intended lockfile."""
    return sorted((distribution.metadata.get("Name", "unknown").lower(), distribution.version) for distribution in package_metadata.distributions())


def _inside(raw_dir, entry):
    root = Path(raw_dir).resolve()
    target = (root / entry["path"]).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError("Arquivo fora do diretório raw autorizado")
    return target


def select_pilot(rows, *, sample_size=40):
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    buckets = defaultdict(list)
    for row in sorted(rows, key=lambda item: item["mfiles_id"]):
        for route in sorted({entry["parse_route"] for entry in row.get("files", [])}):
            buckets[route].append(row)
    selected = {}
    while len(selected) < min(sample_size, len(rows)):
        progress = False
        for route in sorted(buckets):
            bucket = buckets[route]
            while bucket and bucket[0]["mfiles_id"] in selected:
                bucket.pop(0)
            if bucket and len(selected) < sample_size:
                item = bucket.pop(0)
                selected[item["mfiles_id"]] = item
                progress = True
        if not progress:
            break
    return sorted(selected.values(), key=lambda row: row["mfiles_id"])


def stage_records(rows, *, raw_dir, snapshot, configuration_version, timeout_seconds=120, on_file=None):
    entries = []
    keys = set()
    for row in rows:
        if row.get("decision") != "include":
            raise ValueError("Staging aceita somente include da curadoria verificada")
        if not row.get("files"):
            raise ValueError("Instrumento include sem inventário de arquivos")
        for entry in row["files"]:
            path = _inside(raw_dir, entry)
            if not path.is_file() or _hash(path) != entry["sha256"]:
                raise ValueError("Arquivo ausente ou hash divergente")
            key = (row["mfiles_id"], row["mfiles_version"], entry["file_id"])
            if key in keys:
                raise ValueError("Arquivo duplicado no inventário")
            keys.add(key)
            entries.append((row, entry, path))
    files = []
    for row, entry, path in entries:
        identity = f"{snapshot}:{row['mfiles_id']}:{row['mfiles_version']}:{entry['file_id']}:{entry['sha256']}"
        source_id = hashlib.sha256(identity.encode()).hexdigest()
        try:
            result = parse_verified_file(entry, path=path, row=row, raw_dir=raw_dir, timeout_seconds=timeout_seconds)
        except Exception:
            result = {"technical_status": "failed", "eligibility": "failed", "risks": ["worker_failed"]}
        if _hash(path) != entry["sha256"]:
            raise ValueError("hash mudou durante parsing; lote não é publicável")
        # A worker cannot grant approval. This staging gate is deliberately restrictive.
        if not isinstance(result, dict) or result.get("eligibility") not in {"pending_review", "quarantined", "failed"}:
            result = {"technical_status": "failed", "eligibility": "failed", "risks": ["invalid_worker_result"]}
        result = {**result, "source_id": source_id, "mfiles_id": row["mfiles_id"], "mfiles_version": row["mfiles_version"], "file_id": entry["file_id"], "file_hash": entry["sha256"], "parse_route": entry["parse_route"], "snapshot": snapshot, "configuration_version": configuration_version}
        files.append(result)
        if on_file:
            on_file(result)
    return {"schema_version": "staging-v1", "snapshot": snapshot, "configuration_version": configuration_version, "published": False, "documents": len(rows), "files": files, "counts": dict(Counter(result["eligibility"] for result in files))}


def parse_verified_file(entry, *, path, row, raw_dir, timeout_seconds):
    from app.ingestion.file_format import detect_format
    from app.ingestion.legacy_parser import find_libreoffice_binary
    info = detect_format(path, entry.get("declared_extension", path.suffix))
    if not info.parseable or info.route.value != entry["parse_route"] or info.detected.value != entry.get("detected_format"):
        return {"technical_status": "failed", "eligibility": "failed", "risks": ["format_route_mismatch"]}
    if info.route.value == "libreoffice" and not find_libreoffice_binary():
        return {"technical_status": "unavailable", "eligibility": "failed", "risks": ["libreoffice_unavailable"]}
    if path.stat().st_size > 64 * 1024 * 1024:
        return {"technical_status": "failed", "eligibility": "failed", "risks": ["source_size_limit"]}
    payload = {"path": str(path), "raw_dir": str(Path(raw_dir).resolve()), "row": row, "entry": entry}
    environment = os.environ.copy()
    environment.update(LANGSMITH_TRACING="false", LANGSMITH_TRACING_V2="false", LANGCHAIN_TRACING_V2="false", LANGSMITH_API_KEY="", LANGCHAIN_API_KEY="", OPENAI_API_KEY="", ANONYMIZED_TELEMETRY="false")
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        worker = subprocess.run([sys.executable, "-m", "app.ingestion.batch", "_worker"], input=json.dumps(payload), text=True, encoding="utf-8", capture_output=True, timeout=timeout_seconds, env=environment, cwd=Path(__file__).resolve().parents[2])
    except subprocess.TimeoutExpired:
        return {"technical_status": "failed", "eligibility": "failed", "risks": ["parser_timeout"]}
    if worker.returncode:
        return {"technical_status": "failed", "eligibility": "failed", "risks": ["worker_failed"]}
    return json.loads(worker.stdout)


def _worker():
    import ipaddress
    import socket
    from app.config import Settings, get_settings
    Settings.model_config = Settings.model_config | {"env_file": None}
    get_settings.cache_clear()
    def local(address):
        if not isinstance(address, tuple):
            return True
        try:
            return ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            return address[0] == "localhost"
    for name in ("connect", "connect_ex", "sendto"):
        original = getattr(socket.socket, name)
        def guarded(connection, *args, _original=original, _name=name, **kwargs):
            address = args[-1] if _name == "sendto" else args[0]
            if not local(address):
                raise RuntimeError("offline worker")
            return _original(connection, *args, **kwargs)
        setattr(socket.socket, name, guarded)
    resolve = socket.getaddrinfo
    def local_resolve(host, *args, **kwargs):
        if host is not None and not local((host, 0)):
            raise RuntimeError("offline worker DNS")
        return resolve(host, *args, **kwargs)
    socket.getaddrinfo = local_resolve
    from app.ingestion.parsing_pipeline import parse_single_document
    from app.ingestion.content_validation import validate_content
    payload = json.load(sys.stdin)
    row, entry = payload["row"], payload["entry"]
    verified_path = _inside(payload["raw_dir"], entry)
    if verified_path != Path(payload["path"]).resolve() or _hash(verified_path) != entry["sha256"]:
        raise ValueError("Worker source identity mismatch")
    doc = parse_single_document(payload["path"], mfiles_record=row, doc_id=row["mfiles_id"], doc_version=row["mfiles_version"], parse_route=entry["parse_route"])
    doc.file_id = str(entry["file_id"])
    mapping = {block.block_id: f"file-{entry['file_id']}:{block.block_id}" for block in doc.blocks}
    for block in doc.blocks:
        block.block_id = mapping[block.block_id]
        if block.parent_clause_id:
            block.parent_clause_id = mapping.get(block.parent_clause_id, block.parent_clause_id)
    doc.metadata.temporal_mentions = [
        mention.model_copy(update={"block_id": mapping[mention.block_id]})
        for mention in doc.metadata.temporal_mentions
    ]
    # Namespace references together; changing block IDs must not leave dangling
    # temporal evidence, even when two files belong to the same instrument.
    doc = type(doc).model_validate(doc.model_dump())
    report = validate_content(doc)
    doc.eligibility = report.eligibility
    print(json.dumps({"technical_status": doc.status, "eligibility": report.eligibility, "risks": report.risks, "parsed_document": doc.model_dump(mode="json")}, ensure_ascii=False))


def preflight(rows, *, raw_dir, curation_dir, timeout_seconds=120):
    from app.ingestion.legacy_parser import find_libreoffice_binary
    libreoffice = find_libreoffice_binary()
    tesseract = shutil.which("tesseract")
    tools = {}
    for name, command in (("libreoffice", libreoffice), ("tesseract", tesseract)):
        if not command:
            tools[name] = {"available": False, "version": None}
            continue
        try:
            result = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=10)
            tools[name] = {"available": result.returncode == 0, "version": result.stdout.splitlines()[0] if result.stdout else None}
        except (OSError, subprocess.TimeoutExpired):
            tools[name] = {"available": False, "version": None}
    languages = []
    if tesseract:
        try:
            result = subprocess.run([tesseract, "--list-langs"], capture_output=True, text=True, timeout=10)
            languages = result.stdout.splitlines()[1:] if result.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired):
            languages = []
    parser_files = ["schemas.py", "docx_parser.py", "legacy_parser.py", "pdf_parser.py", "metadata_extractor.py", "temporal.py", "parsing_pipeline.py", "content_validation.py", "batch.py", "batch_diagnostics.py", "near_dup.py", "review_store.py", "file_format.py", "curation.py"]
    parser_hashes = {name: _hash(Path(__file__).parent / name) for name in parser_files}
    parser_hashes["identifiers.py"] = _hash(Path(__file__).resolve().parents[1] / "identifiers.py")
    parser_hashes["config.py"] = _hash(Path(__file__).resolve().parents[1] / "config.py")
    inputs = {"manifest_sha256": _hash(Path(raw_dir) / "manifest.jsonl"), "curation_sha256": _hash(Path(curation_dir) / "curation.jsonl"), "lock_sha256": _hash(Path(__file__).resolve().parents[2] / "uv.lock")}
    runtime = {"python": platform.python_version(), "platform": platform.system(), "machine": platform.machine(), "python_implementation": platform.python_implementation(), "installed_packages": _installed_packages()}
    configuration = {"identity_schema": "preflight-v2", "parser_hashes": parser_hashes, "tools": tools, "ocr_por_available": "por" in languages, "source_limit_bytes": 64 * 1024 * 1024, "worker_concurrency": 1, "worker_timeout_seconds": timeout_seconds, "runtime": runtime, **inputs}
    configuration_version = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    return {"schema_version": "preflight-v2", "configuration_version": configuration_version, **configuration, "python": runtime["python"], "platform": runtime["platform"], "documents": len(rows), "files": sum(len(row["files"]) for row in rows), "routes": dict(Counter(entry["parse_route"] for row in rows for entry in row["files"]))}


def _write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "pilot", "run", "_worker"])
    parser.add_argument("--sample-size", type=int, default=40)
    parser.add_argument("--staging-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.command == "_worker":
        _worker()
        return
    if arguments.command == "run" and not arguments.staging_only:
        parser.error("run exige --staging-only; publicação não existe neste módulo")
    from app.config import get_settings
    from app.ingestion.curation import load_curated
    settings = get_settings()
    rows = load_curated(settings.curation_dir, settings.raw_data_dir, verify_files=True)
    run_id = str(uuid4())
    destination = settings.staging_dir / run_id
    destination.mkdir(parents=True, exist_ok=False)
    report = preflight(rows, raw_dir=settings.raw_data_dir, curation_dir=settings.curation_dir, timeout_seconds=settings.parser_timeout_seconds)
    _write_json(destination / "preflight.json", report)
    if arguments.command == "preflight":
        print(json.dumps({"run_id": run_id, **report}, ensure_ascii=False))
        return
    selected = select_pilot(rows, sample_size=arguments.sample_size) if arguments.command == "pilot" else rows
    completed = 0
    def persist(result):
        nonlocal completed
        _write_json(destination / f"{result['source_id']}.json", result)
        completed += 1
        if completed % 10 == 0:
            print(json.dumps({"processed_files": completed}), flush=True)
    summary = stage_records(selected, raw_dir=settings.raw_data_dir, snapshot=report["manifest_sha256"], configuration_version=report["configuration_version"], timeout_seconds=settings.parser_timeout_seconds, on_file=persist)
    from app.ingestion.batch_diagnostics import diagnose_batch
    diagnostics = diagnose_batch(summary["files"])
    diagnostics.update(snapshot=summary["snapshot"], configuration_version=summary["configuration_version"], run_id=run_id)
    _write_json(destination / "diagnostics.json", diagnostics)
    # Large/raw proofs stay in restricted per-file artifacts; summary contains diagnostics only.
    summary["files"] = [{key: value for key, value in result.items() if key != "parsed_document"} for result in summary["files"]]
    summary["run_id"] = run_id
    summary["created_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(destination / "summary.json", summary)
    print(json.dumps({"run_id": run_id, "documents": summary["documents"], "files": len(summary["files"]), "counts": summary["counts"], "published": False}), flush=True)


if __name__ == "__main__":
    main()
