"""Append-only review ledger bound to exact source/snapshot/hash/configuration.

Imports require explicit provenance, reviewer and reason. Legacy empty QA CSVs
are never treated as approval. This store is not populated automatically by parsers.
"""
import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


ReviewScope = Literal["source", "parties", "unit", "relation", "family"]
SubjectDigest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def review_digest(value) -> str:
    """Bind exact JSON content: dictionary order is irrelevant; array order is not."""
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def unit_review_digest(unit) -> str:
    """Include all provenance/context/dependencies, excluding only the decision."""
    return review_digest(unit.model_dump(mode="json", exclude={"approval_state", "review_record_id"}))


class ReviewSubject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    source_id: str = Field(min_length=1)
    scope: ReviewScope = "source"
    subject_id: str | None = Field(default=None, min_length=1)
    subject_digest: SubjectDigest | None = None

    @model_validator(mode="after")
    def exact_scope(self):
        if self.scope == "source":
            if self.subject_id not in (None, self.source_id) or self.subject_digest is not None:
                raise ValueError("Escopo source não admite outro sujeito ou digest derivado")
            # One source key, even when a caller explicitly repeats its identity.
            object.__setattr__(self, "subject_id", None)
        elif self.subject_id is None or self.subject_digest is None:
            raise ValueError("Escopo derivado exige sujeito e digest SHA256")
        elif self.scope == "parties" and self.subject_id != self.source_id:
            raise ValueError("Decisão de partes exige o sujeito da própria fonte")
        return self

    @property
    def ledger_key(self):
        return self.source_id, self.scope, self.subject_id or ""


class ReviewRecord(ReviewSubject):
    record_id: str = Field(min_length=1)
    snapshot: str = Field(min_length=1)
    file_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration_version: str = Field(min_length=1)
    decision: Literal["approved", "quarantined", "excluded"]
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    recorded_at: AwareDatetime
    supersedes_record_id: str | None = None


class ReviewStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS reviews (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL UNIQUE,
                    source_id TEXT NOT NULL,
                    snapshot TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    configuration_version TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS review_source ON reviews(source_id, snapshot, file_hash, configuration_version, sequence);
                CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON reviews BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
                CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON reviews BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
            """)
            # Add only index metadata. Legacy payloads and append-only triggers remain
            # intact; historical records deserialize with source scope defaults.
            database.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in database.execute("PRAGMA table_info(reviews)")}
            if "scope" not in columns:
                database.execute("ALTER TABLE reviews ADD COLUMN scope TEXT NOT NULL DEFAULT 'source'")
            if "subject_id" not in columns:
                database.execute("ALTER TABLE reviews ADD COLUMN subject_id TEXT NOT NULL DEFAULT ''")
            database.execute("CREATE INDEX IF NOT EXISTS review_subject ON reviews(source_id, scope, subject_id, sequence)")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def record(self, *, source_id, snapshot, file_hash, configuration_version, decision, reviewer, reason,
               scope: ReviewScope = "source", subject_id=None, subject_digest=None):
        subject = ReviewSubject(source_id=source_id, scope=scope, subject_id=subject_id, subject_digest=subject_digest)
        values = {**subject.model_dump(), "snapshot": snapshot, "file_hash": file_hash, "configuration_version": configuration_version, "decision": decision, "reviewer": reviewer, "reason": reason, "record_id": str(uuid4()), "recorded_at": datetime.now(timezone.utc)}
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            previous = database.execute("SELECT record_id FROM reviews WHERE source_id=? AND scope=? AND subject_id=? ORDER BY sequence DESC LIMIT 1", subject.ledger_key).fetchone()
            item = ReviewRecord(**values, supersedes_record_id=previous[0] if previous else None)
            self._insert(database, item)
            return item

    @staticmethod
    def _insert(database, record):
        existing = database.execute("SELECT payload FROM reviews WHERE record_id=?", (record.record_id,)).fetchone()
        if existing:
            if ReviewRecord.model_validate_json(existing[0]) != record:
                raise ValueError("Conflito de ID do registro de revisão")
            return
        latest = database.execute("SELECT payload FROM reviews WHERE source_id=? AND scope=? AND subject_id=? ORDER BY sequence DESC LIMIT 1", record.ledger_key).fetchone()
        previous = ReviewRecord.model_validate_json(latest[0]) if latest else None
        if record.supersedes_record_id != (previous.record_id if previous else None) or (previous and record.recorded_at < previous.recorded_at):
            raise ValueError("Importação diverge do histórico de revisão vigente")
        database.execute("INSERT INTO reviews(record_id,source_id,snapshot,file_hash,configuration_version,payload,scope,subject_id) VALUES(?,?,?,?,?,?,?,?)", (record.record_id, record.source_id, record.snapshot, record.file_hash, record.configuration_version, record.model_dump_json(), record.scope, record.subject_id or ""))

    def latest(self, *, source_id, snapshot, file_hash, configuration_version,
               scope: ReviewScope = "source", subject_id=None, subject_digest=None):
        subject = ReviewSubject(source_id=source_id, scope=scope, subject_id=subject_id, subject_digest=subject_digest)
        with self._connect() as database:
            result = database.execute("SELECT payload FROM reviews WHERE source_id=? AND scope=? AND subject_id=? ORDER BY sequence DESC LIMIT 1", subject.ledger_key).fetchone()
        if not result:
            return None
        item = ReviewRecord.model_validate_json(result[0])
        return item if item.ledger_key == subject.ledger_key and (item.snapshot, item.file_hash, item.configuration_version, item.subject_digest) == (snapshot, file_hash, configuration_version, subject.subject_digest) else None

    def require_current(self, *, record_id, source_id, snapshot, file_hash, configuration_version,
                        scope: ReviewScope = "source", subject_id=None, subject_digest=None) -> ReviewRecord:
        """Authorize only an exact approved decision at the current scoped head."""
        item = self.latest(source_id=source_id, snapshot=snapshot, file_hash=file_hash,
                           configuration_version=configuration_version, scope=scope,
                           subject_id=subject_id, subject_digest=subject_digest)
        if item is None or not record_id or item.record_id != record_id or item.decision != "approved":
            raise ValueError("Decisão ausente, inválida, obsoleta ou não aprovada no escopo vigente")
        return item

    def history(self, source_id):
        with self._connect() as database:
            return [ReviewRecord.model_validate_json(row[0]) for row in database.execute("SELECT payload FROM reviews WHERE source_id=? ORDER BY sequence", (source_id,))]

    def state_digest(self) -> str:
        """Conservative fingerprint of every current head, in one read snapshot.

        Any scoped decision change invalidates consumers, including changes outside
        their selected subgraph. Optimizing that scope is deferred until P4.
        """
        return self._heads_digest()

    def relation_state_digest(self) -> str:
        """Bind a family resolution to all relation heads, including rejected ones.

        Reviewing the family itself cannot change the registry it adjudicates.
        A newly registered relation or any relation revision requires adjudication
        against a fresh registry digest, regardless of caller snapshot.
        """
        return self._heads_digest(scope="relation")

    def _heads_digest(self, *, scope: ReviewScope | None = None) -> str:
        heads = []
        with self._connect() as database:
            database.execute("BEGIN")
            rows = database.execute("""
                SELECT record_id, source_id, snapshot, file_hash, configuration_version,
                       scope, subject_id, payload
                FROM reviews
                WHERE (? IS NULL OR scope = ?) AND sequence IN (
                    SELECT MAX(sequence) FROM reviews GROUP BY source_id, scope, subject_id
                )
                ORDER BY source_id, scope, subject_id
            """, (scope, scope)).fetchall()
            for row in rows:
                item = ReviewRecord.model_validate_json(row[7])
                expected = (item.record_id, item.source_id, item.snapshot, item.file_hash,
                            item.configuration_version, item.scope, item.subject_id or "")
                if row[:7] != expected:
                    raise ValueError("Head do ledger diverge da identidade e do escopo de seu payload")
                heads.append(item.model_dump(mode="json"))
        return review_digest(heads)

    def export_csv(self, target: Path):
        with self._connect() as database:
            items = [ReviewRecord.model_validate_json(row[0]).model_dump(mode="json") for row in database.execute("SELECT payload FROM reviews ORDER BY sequence")]
        with Path(target).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["_csv_encoding", *ReviewRecord.model_fields])
            writer.writeheader()
            for item in items:
                writer.writerow({"_csv_encoding": "review-quote-v2", **{key: "'" + value if isinstance(value, str) and value.startswith(("'", "=", "+", "-", "@")) else value for key, value in item.items()}})

    def import_csv(self, source: Path):
        with Path(source).open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError("CSV sem cabeçalho único e inequívoco")
            rows = list(reader)
        # Validate the complete import before a transaction; reject empty/legacy data.
        items = []
        if not rows:
            raise ValueError("Importação sem registros de revisão")
        for row in rows:
            if row.pop("_csv_encoding", None) != "review-quote-v2":
                raise ValueError("CSV requer encoding versionado; migrar legado com proveniência explícita")
            values = {key: value[1:] if isinstance(value, str) and value.startswith(("''", "'=", "'+", "'-", "'@")) else value for key, value in row.items()}
            if not values.get("supersedes_record_id"):
                values["supersedes_record_id"] = None
            for key in ("subject_id", "subject_digest"):
                if key in values and not values[key]:
                    values[key] = None
            items.append(ReviewRecord.model_validate(values))
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            for item in items:
                self._insert(database, item)
