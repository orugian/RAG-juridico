"""
Sincronização incremental do acervo contratual M-Files -> disco local (data/raw).

Layout gerado:
    data/raw/manifest.jsonl                       # 1 registro JSON por documento M-Files
    data/raw/files/{doc_id}/{file_id}_v{ver}.{ext} # binário original (docx, pdf, doc...)

O manifest é a fonte de metadados para os Document Loaders (cabeçalho de contexto
dos chunks e filtros por cliente). Documentos cuja versão não mudou não são baixados novamente.

Uso:
    uv run python -m app.ingestion.sync [--force] [--classes "Contrato" "Acordo"]
"""

import argparse
import hashlib
import json
import shutil
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings
from app.ingestion.mfiles_client import MFilesClient, MFilesDocument, MFilesError
from app.monitoring import logger

MANIFEST_NAME = "manifest.jsonl"
CHECKPOINT_EVERY = 25


def load_manifest(raw_dir: Path) -> dict[int, dict[str, Any]]:
    path = raw_dir / MANIFEST_NAME
    if not path.exists():
        return {}
    records = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {r["mfiles_id"]: r for r in records}


def write_manifest(raw_dir: Path, records: list[dict[str, Any]]) -> None:
    tmp = raw_dir / f"{MANIFEST_NAME}.tmp"
    tmp.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sorted(records, key=lambda r: r["mfiles_id"])),
        encoding="utf-8",
    )
    tmp.replace(raw_dir / MANIFEST_NAME)


def _is_up_to_date(previous: Optional[dict[str, Any]], document: MFilesDocument, raw_dir: Path) -> bool:
    return (
        previous is not None
        and previous["mfiles_version"] == document.version
        and previous["status"] == "ok"
        and all((raw_dir / f["path"]).exists() for f in previous["files"])
    )


def _properties_to_dict(properties: list[dict[str, Any]], property_names: dict[int, str]) -> dict[str, str]:
    result = {}
    for prop in properties:
        value = prop.get("TypedValue", {}).get("DisplayValue")
        if value not in (None, ""):
            result[property_names.get(prop["PropertyDef"], f"p{prop['PropertyDef']}")] = value
    return result


def _fetch_document(
    client: MFilesClient,
    document: MFilesDocument,
    class_name: str,
    property_names: dict[int, str],
    raw_dir: Path,
) -> dict[str, Any]:
    doc_dir = raw_dir / "files" / str(document.id)
    shutil.rmtree(doc_dir, ignore_errors=True)
    doc_dir.mkdir(parents=True, exist_ok=True)

    files = []
    for f in document.files:
        content = client.download_file(document, f)
        target = doc_dir / f"{f.id}_v{document.version}.{f.extension or 'bin'}"
        target.write_bytes(content)
        files.append({
            **asdict(f),
            "path": target.relative_to(raw_dir).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "downloaded_bytes": len(content),
        })

    return {
        "mfiles_id": document.id,
        "mfiles_version": document.version,
        "class_id": document.class_id,
        "class_name": class_name,
        "title": document.title,
        "last_modified_utc": document.last_modified_utc,
        "properties": _properties_to_dict(client.get_properties(document), property_names),
        "files": files,
        "status": "ok" if files else "no_files",
        "error": None,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


def sync(
    client: MFilesClient,
    raw_dir: Path,
    class_names: list[str],
    force: bool = False,
) -> Counter:
    raw_dir.mkdir(parents=True, exist_ok=True)
    previous = load_manifest(raw_dir)
    classes = client.resolve_class_ids(class_names)
    missing = {n.casefold() for n in class_names} - {n.casefold() for n in classes.values()}
    if missing:
        logger.warning("Classes não encontradas no vault", extra={"classes": sorted(missing)})
    property_names = client.get_property_definitions()

    stats: Counter = Counter()
    records: list[dict[str, Any]] = []
    checkpoint = dict(previous)
    for class_id, class_name in classes.items():
        for document in client.list_documents(class_id):
            stats["listed"] += 1
            prev = previous.get(document.id)
            if not force and _is_up_to_date(prev, document, raw_dir):
                records.append(prev)
                stats["unchanged"] += 1
                continue
            try:
                record = _fetch_document(client, document, class_name, property_names, raw_dir)
                stats["downloaded" if record["status"] == "ok" else "no_files"] += 1
            except MFilesError as exc:
                logger.error("Falha ao extrair documento", extra={"mfiles_id": document.id, "error": str(exc)})
                record = {
                    "mfiles_id": document.id,
                    "mfiles_version": document.version,
                    "class_id": class_id,
                    "class_name": class_name,
                    "title": document.title,
                    "files": [],
                    "status": "failed",
                    "error": str(exc),
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                }
                stats["failed"] += 1
            records.append(record)
            checkpoint[record["mfiles_id"]] = record
            if len(records) % CHECKPOINT_EVERY == 0:
                write_manifest(raw_dir, list(checkpoint.values()))

    current_ids = {r["mfiles_id"] for r in records}
    for stale_id in previous.keys() - current_ids:
        shutil.rmtree(raw_dir / "files" / str(stale_id), ignore_errors=True)
        stats["removed"] += 1

    write_manifest(raw_dir, records)
    for r in records:
        for f in r["files"]:
            stats[f"ext:{f['extension']}"] += 1
    logger.info("Sincronização M-Files concluída", extra={"stats": dict(stats)})
    return stats


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Extrai o acervo contratual do M-Files para data/raw.")
    parser.add_argument("--classes", nargs="+", default=settings.mfiles_sync_classes)
    parser.add_argument("--force", action="store_true", help="Baixa novamente todos os documentos.")
    parser.add_argument("--raw-dir", type=Path, default=settings.raw_data_dir)
    args = parser.parse_args()

    with MFilesClient(settings) as client:
        stats = sync(client, args.raw_dir, args.classes, force=args.force)
    print(json.dumps(dict(stats), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
