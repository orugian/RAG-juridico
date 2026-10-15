"""Publication: workbench decisions + staging -> new governed generation -> switch.

    python -m app.publish --data-root ~/rag-juridico/data --reviewer NOME --reason "..." [--activate]

Layout under ``--data-root``: staging/<id>/, raw/files/, curation/curation.jsonl,
conversions/, models/<qwen>/, workbench.sqlite, embedding-cache/, pubs/<name>/
{authority,bundle,indexes} and current.json (the published pointer).

Each publication gets a fresh ledger and a copy of the live policy journal, so
the serving generation is never invalidated while the next one is built. The
accepted P4 manager builds and promotes; only the embedding computation is
cached (by text and exact encoder identity). ``--activate`` stops the service,
re-copies the live journal (blocks made during the build are kept), moves the
pointer and starts the service again (about a minute of downtime).
"""
import argparse
from array import array
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time

from app.corpus_build import (Approval, LegacyConverter, build_reviewed_bundle, load_curation_rows,
                              load_staged_sources, qwen_identity, write_outputs)

QWEN_DIR = "qwen3-embedding-0.6b-97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
GENERATION_ID = "gen-01"
SERVICE_UNIT = "rag-juridico.service"


class CachedEmbeddings:
    """Embedding cache keyed by text digest, one file per exact encoder identity.

    Identity is computed from the verified artifact without loading weights; the
    encoder is loaded only on a cache miss and must report the same identity.
    """

    def __init__(self, artifact: Path, cache_dir: Path, *, factory=None, identity=None):
        self.artifact = Path(artifact)
        self._identity = identity or qwen_identity(self.artifact)
        fingerprint = hashlib.sha256(json.dumps(self._identity, sort_keys=True).encode()).hexdigest()
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.path = cache_dir / f"{fingerprint[:24]}.sqlite"
        self._factory = factory
        self._encoder = None
        self._lock = threading.Lock()
        self.hits = self.misses = 0
        with sqlite3.connect(self.path) as database:
            database.execute("CREATE TABLE IF NOT EXISTS vectors(text_sha256 TEXT PRIMARY KEY, dimension INTEGER, vector BLOB)")

    @property
    def identity(self):
        return deepcopy(self._identity)

    def _load(self):
        if self._encoder is None:
            if self._factory is not None:
                self._encoder = self._factory()
            else:
                from app.embeddings.qwen import QwenEmbeddings
                self._encoder = QwenEmbeddings(self.artifact)
            if self._encoder.identity != self._identity:
                raise ValueError("embedding_cache_identity_mismatch")
        return self._encoder

    @staticmethod
    def _key(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def lookup(self, texts):
        keys = [self._key(text) for text in texts]
        found = {}
        with sqlite3.connect(self.path) as database:
            for offset in range(0, len(keys), 500):
                part = keys[offset:offset + 500]
                rows = database.execute(f"SELECT text_sha256, vector FROM vectors WHERE text_sha256 IN ({','.join('?' * len(part))})", part)
                for key, blob in rows:
                    found[key] = array("f", blob).tolist()
        return keys, found

    def store(self, texts, vectors):
        rows = [(self._key(text), len(vector), array("f", vector).tobytes()) for text, vector in zip(texts, vectors)]
        with sqlite3.connect(self.path) as database:
            database.executemany("INSERT OR REPLACE INTO vectors VALUES(?,?,?)", rows)

    def embed_documents(self, texts):
        with self._lock:
            keys, found = self.lookup(texts)
            missing = [text for text, key in zip(texts, keys) if key not in found]
            self.hits += len(texts) - len(missing)
            self.misses += len(missing)
            if missing:
                computed = self._load().embed_documents(missing)
                self.store(missing, computed)
                keys_missing = [self._key(text) for text in missing]
                found.update({key: array("f", vector).tolist() for key, vector in zip(keys_missing, computed)})
            return [found[key] for key in keys]

    def embed_query(self, text):
        return self._load().embed_query(text)

    def seed_from_generation(self, generation_dir: Path, configuration) -> int:
        """Reuse vectors of an existing generation built with the same encoder identity."""
        if configuration.embedding_identity != self._identity:
            return 0
        from app.retrieval.generation_contracts import GenerationBundle
        bundle = GenerationBundle.model_validate_json((Path(generation_dir) / "bundle.json").read_bytes())
        vectors = json.loads((Path(generation_dir) / "vectors.json").read_bytes())
        if isinstance(vectors, dict) and "vectors" in vectors:
            vectors = vectors["vectors"]
        texts, rows = [], []
        for chunk in bundle.chunks:
            if chunk.chunk_id in vectors:
                texts.append(chunk.text_search)
                rows.append(vectors[chunk.chunk_id])
        self.store(texts, rows)
        return len(texts)


def _now_name():
    return "pub-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def read_current(data_root: Path):
    pointer = Path(data_root) / "current.json"
    if not pointer.is_file():
        return None
    value = json.loads(pointer.read_text(encoding="utf-8"))
    if set(value) != {"pub", "journal_id"} or "/" in value["pub"] or "\\" in value["pub"] or value["pub"].startswith("."):
        raise ValueError("current_pointer_invalid")
    return value


def write_current(data_root: Path, pub: str, journal_id: str):
    pointer = Path(data_root) / "current.json"
    temporary = pointer.with_suffix(".tmp")
    temporary.write_text(json.dumps({"pub": pub, "journal_id": journal_id}), encoding="utf-8")
    os.replace(temporary, pointer)


def copy_journal(source_dir: Path, target_dir: Path):
    """Consistent copy of the policy journal (SQLite backup + checkpoint/lock files)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    source, target = Path(source_dir) / "policy.sqlite", Path(target_dir) / "policy.sqlite"
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(target) + suffix)
        if candidate.exists():
            candidate.unlink()
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as reader, sqlite3.connect(target) as writer:
        reader.backup(writer)
    for name in ("policy.sqlite.checkpoint.json", "policy.sqlite.lock"):
        shutil.copy2(Path(source_dir) / name, Path(target_dir) / name)


def _service(action: str):
    return subprocess.run(["systemctl", "--user", action, SERVICE_UNIT], capture_output=True, text=True, timeout=120)


def publish(data_root: Path, *, reviewer: str, reason: str, accept_metadata_mismatch=True,
            self_bind_unresolved_references=True, previous_authority: Path | None = None,
            journal_id: str | None = None, soffice: str | None = None, activate=False, log=print,
            embeddings=None, vector_store=None, budget=None, name: str | None = None, service=None,
            publication_id: str | None = None):
    from app.contracts import AccessContext
    from app.ingestion.evidence import QwenTokenBudget
    from app.ingestion.review_store import ReviewStore
    from app.retrieval.generation_contracts import GenerationConfig
    from app.retrieval.policy import SQLitePolicyJournal
    from app.retrieval.review_authority import ReadOnlyReviewAuthority
    from app.retrieval.vector_store import ChromaVectorStore
    from app.serving import ServingGenerationManager
    from app.workbench.store import WorkbenchStore
    data_root = Path(data_root)
    started = time.monotonic()
    current = read_current(data_root)
    if current:
        previous_authority = data_root / "pubs" / current["pub"] / "authority"
        journal_id = current["journal_id"]
    if journal_id is None:
        raise ValueError("journal_id_required_for_first_publication")
    name = name or _now_name()
    pub = data_root / "pubs" / name
    authority, bundle_dir, indexes = pub / "authority", pub / "bundle", pub / "indexes"
    pub.mkdir(parents=True, exist_ok=False)
    if previous_authority is not None:
        copy_journal(Path(previous_authority), authority)
    journal = SQLitePolicyJournal(authority / "policy.sqlite", initialize=previous_authority is None,
                                  journal_id=journal_id)
    workbench = WorkbenchStore(data_root / "workbench.sqlite")
    publication_id = publication_id or workbench.start_publication(name, reviewer)
    try:
        approval = Approval(reviewer, reason, self_bind_unresolved_references=self_bind_unresolved_references)
        staging = next(path for path in sorted((data_root / "staging").iterdir()) if path.is_dir())
        legacy = None
        if soffice:
            legacy = LegacyConverter(load_curation_rows(data_root / "curation" / "curation.jsonl"),
                                     data_root / "conversions", soffice)
        staged, skipped = load_staged_sources(staging, data_root / "raw" / "files",
                                              accept_metadata_mismatch=accept_metadata_mismatch, legacy=legacy)
        log(f"staged={len(staged)} skipped={len(skipped)}")
        artifact = data_root / "models" / QWEN_DIR
        budget = budget or QwenTokenBudget(artifact)
        decisions = workbench.decisions()
        # A source blocked in the live journal is never republished.
        for source_id in journal.snapshot().blocked_source_ids:
            decisions.excluded.setdefault(source_id, "blocked_in_policy_journal")
        result = build_reviewed_bundle(staged, ledger=ReviewStore(authority / "reviews.sqlite"), budget=budget,
                                       approval=approval, decisions=decisions)
        report = write_outputs(bundle_dir, result, qwen_artifact=artifact, approval=approval,
                               accept_metadata_mismatch=accept_metadata_mismatch, skipped=skipped,
                               staged_count=len(staged) + len(skipped), journal_id=journal_id)
        log(f"bundle sources={report['eligible_sources']} units={report['units']} chunks={report['chunks']} "
            f"reserved={len(result.reserved)} rejected={len(result.rejected)} relations={result.relations}")
        configuration = GenerationConfig.model_validate_json((bundle_dir / "configuration.json").read_bytes())
        embeddings = embeddings or CachedEmbeddings(artifact, data_root / "embedding-cache",
                                                    identity=configuration.embedding_identity)
        if current and hasattr(embeddings, "seed_from_generation"):
            previous_generation = data_root / "pubs" / current["pub"] / "indexes" / GENERATION_ID
            previous_config = GenerationConfig.model_validate_json(
                (data_root / "pubs" / current["pub"] / "bundle" / "configuration.json").read_bytes())
            log(f"embedding cache seeded={embeddings.seed_from_generation(previous_generation, previous_config)}")
        manager = ServingGenerationManager(
            indexes, configuration=configuration, embeddings=embeddings,
            vector_store=vector_store or ChromaVectorStore(mode="embedded", dimension=configuration.embedding_dimension),
            ledger=ReadOnlyReviewAuthority(authority / "reviews.sqlite"), journal=journal, journal_id=journal_id)
        from app.ingestion.schemas import ParsedDocument
        manager.build(GENERATION_ID, result.bundle, source_paths=result.source_paths,
                      source_documents={key: ParsedDocument.model_validate(value.model_dump())
                                        for key, value in result.source_documents.items()} or None,
                      conversion_paths=result.conversion_paths or None)
        log(f"built hits={getattr(embeddings, 'hits', None)} misses={getattr(embeddings, 'misses', None)}")
        snapshot = journal.snapshot()
        operator = AccessContext(principal_id="publisher", credential_id="publisher", permissions=["operate"],
                                 policy_epoch=snapshot.policy_epoch, access_scope_digest="publisher")
        manifest = manager.promote(GENERATION_ID, access=operator)
        report |= {"publication": name, "generation_id": GENERATION_ID, "manifest_generation": manifest.generation_id,
                   "build_seconds": round(time.monotonic() - started, 1),
                   "embedding_cache": {"hits": getattr(embeddings, "hits", None), "misses": getattr(embeddings, "misses", None)}}
        (bundle_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        state = "promoted"
        if activate:
            activate_publication(data_root, name, journal_id, log=log, service=service or _service)
            state = "published"
        workbench.finish_publication(publication_id, state, report)
        log(f"{state} {name} in {report['build_seconds']} s")
        return report
    except Exception as error:
        state = "activation_failed" if str(error) == "service_start_failed" else "failed"
        workbench.finish_publication(publication_id, state, {"error": type(error).__name__,
                                                             "detail": str(error)[:300]})
        raise


def activate_publication(data_root: Path, name: str, journal_id: str, *, log=print, service=_service):
    data_root = Path(data_root)
    target = data_root / "pubs" / name
    if not re.fullmatch(r"pub-[A-Za-z0-9_-]+", name) or not (target / "indexes" / "active.json").is_file():
        raise ValueError("publication_not_promoted")
    current = read_current(data_root)
    stopped = service("stop")
    log(f"service stop rc={stopped.returncode}")
    try:
        if current:
            copy_journal(data_root / "pubs" / current["pub"] / "authority", target / "authority")
        write_current(data_root, name, journal_id)
    finally:
        started = service("start")
        log(f"service start rc={started.returncode}")
    if started.returncode != 0:
        raise RuntimeError("service_start_failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--previous-authority", help="First publication only: authority dir whose journal is kept")
    parser.add_argument("--journal-id", help="First publication only")
    parser.add_argument("--soffice", default=os.environ.get("RAG_SOFFICE"))
    parser.add_argument("--strict", action="store_true", help="Do not accept metadata mismatch nor self-bind references")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--activate-only", metavar="PUB", help="Switch the service to an already promoted publication")
    parser.add_argument("--name", help="Publication name (pub-...)")
    parser.add_argument("--publication-id", help="Workbench publication row created by the requester")
    arguments = parser.parse_args(argv)
    if arguments.name and not re.fullmatch(r"pub-[A-Za-z0-9_-]+", arguments.name):
        parser.error("invalid publication name")
    if arguments.activate_only:
        current = read_current(Path(arguments.data_root))
        journal_id = current["journal_id"] if current else arguments.journal_id
        activate_publication(Path(arguments.data_root), arguments.activate_only, journal_id)
        return
    publish(Path(arguments.data_root), reviewer=arguments.reviewer, reason=arguments.reason,
            accept_metadata_mismatch=not arguments.strict, self_bind_unresolved_references=not arguments.strict,
            previous_authority=Path(arguments.previous_authority) if arguments.previous_authority else None,
            journal_id=arguments.journal_id, soffice=arguments.soffice, activate=arguments.activate,
            name=arguments.name, publication_id=arguments.publication_id,
            log=lambda message: print(datetime.now().strftime("%H:%M:%S"), message, flush=True))


if __name__ == "__main__":
    main()
