"""Reviewer workbench: request traces, feedback, evaluation cases and pending decisions.

One SQLite file on the serving host (never sent anywhere). Decisions saved here
are *proposals* until a publication turns them into ledger approvals; the
ledger stays the only authority for citations.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid

from app.corpus_build import Decisions, RelationDecision
from app.ingestion.schemas import ContractParty

RELATION_TYPES = ("amends", "supplements", "terminates", "supersedes")
REFERENCE_KINDS = ("bind", "external", "unbound")
FEEDBACK_CATEGORIES = ("correct", "wrong_contract", "missing_clause", "wrong_clause", "wrong_parties",
                       "should_abstain", "should_answer", "other")
EXPECTED_STATUSES = ("answered", "abstained", "needs_clarification")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    request_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, question TEXT NOT NULL, filters TEXT NOT NULL,
    status TEXT, reason_code TEXT, detail_code TEXT, error_code TEXT, generation_id TEXT, cached INTEGER,
    delivered INTEGER NOT NULL DEFAULT 0, latency_ms REAL, timings TEXT, offered TEXT, selected TEXT,
    citations TEXT, response TEXT, llm_raw TEXT, eval_run_id TEXT);
CREATE INDEX IF NOT EXISTS traces_created ON traces(created_at);
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY, request_id TEXT NOT NULL, created_at TEXT NOT NULL, rating TEXT NOT NULL,
    category TEXT NOT NULL, comment TEXT NOT NULL, reviewer TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS eval_cases (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, question TEXT NOT NULL, instrument_ids TEXT NOT NULL,
    expected_status TEXT NOT NULL, expected_instrument_ids TEXT NOT NULL, expected_terms TEXT NOT NULL,
    notes TEXT NOT NULL, feedback_id TEXT, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, finished_at TEXT, generation_id TEXT, state TEXT NOT NULL,
    summary TEXT);
CREATE TABLE IF NOT EXISTS eval_results (
    run_id TEXT NOT NULL, case_id TEXT NOT NULL, request_id TEXT, status TEXT, passed INTEGER NOT NULL,
    checks TEXT NOT NULL, latency_ms REAL, PRIMARY KEY (run_id, case_id));
CREATE TABLE IF NOT EXISTS document_decisions (
    source_id TEXT PRIMARY KEY, title TEXT, parties TEXT, excluded_reason TEXT, reviewer TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reference_decisions (
    source_id TEXT NOT NULL, block_id TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
    literal TEXT NOT NULL, kind TEXT NOT NULL, target_block_ids TEXT NOT NULL, reviewer TEXT NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY (source_id, block_id, start, end));
CREATE TABLE IF NOT EXISTS relation_decisions (
    relation_key TEXT PRIMARY KEY, from_instrument_id TEXT NOT NULL, to_instrument_id TEXT NOT NULL,
    relation_type TEXT NOT NULL, affected_block_ids TEXT NOT NULL, support_block_ids TEXT NOT NULL,
    reviewer TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS publications (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, finished_at TEXT, state TEXT NOT NULL, name TEXT NOT NULL,
    report TEXT, reviewer TEXT NOT NULL);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class WorkbenchStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as database:
            database.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            with connection:
                yield connection
        finally:
            connection.close()

    def _rows(self, query, parameters=()):
        with self._connect() as database:
            return [dict(row) for row in database.execute(query, parameters)]

    # ----- traces -----------------------------------------------------------
    def record_trace(self, trace: dict):
        columns = ("request_id", "created_at", "question", "filters", "status", "reason_code", "detail_code",
                   "error_code", "generation_id", "cached", "latency_ms", "timings", "offered", "selected",
                   "citations", "response", "llm_raw", "eval_run_id")
        values = {key: trace.get(key) for key in columns}
        values["created_at"] = values["created_at"] or _now()
        for key in ("filters", "timings", "offered", "selected", "citations"):
            values[key] = _json(values[key] if values[key] is not None else ([] if key != "filters" and key != "timings" else {}))
        with self._lock, self._connect() as database:
            database.execute(f"INSERT OR REPLACE INTO traces({','.join(columns)}) VALUES({','.join('?' * len(columns))})",
                             tuple(values[key] for key in columns))

    def mark_delivered(self, request_id: str):
        with self._lock, self._connect() as database:
            database.execute("UPDATE traces SET delivered=1 WHERE request_id=?", (request_id,))

    @staticmethod
    def _decode_trace(row):
        for key in ("filters", "timings", "offered", "selected", "citations"):
            row[key] = json.loads(row[key]) if row.get(key) else ([] if key not in ("filters", "timings") else {})
        row["cached"], row["delivered"] = bool(row["cached"]), bool(row["delivered"])
        return row

    def trace(self, request_id: str):
        rows = self._rows("SELECT * FROM traces WHERE request_id=?", (request_id,))
        if not rows:
            return None
        trace = self._decode_trace(rows[0])
        trace["feedback"] = self._rows("SELECT * FROM feedback WHERE request_id=? ORDER BY created_at", (request_id,))
        return trace

    def traces(self, *, limit=100, status=None, with_feedback=None, include_eval=False):
        clauses, parameters = [], []
        if status:
            clauses.append("t.status=?")
            parameters.append(status)
        if not include_eval:
            clauses.append("t.eval_run_id IS NULL")
        if with_feedback == "negative":
            clauses.append("EXISTS (SELECT 1 FROM feedback f WHERE f.request_id=t.request_id AND f.rating='bad')")
        elif with_feedback == "any":
            clauses.append("EXISTS (SELECT 1 FROM feedback f WHERE f.request_id=t.request_id)")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._rows(f"""SELECT t.request_id, t.created_at, t.question, t.filters, t.status, t.reason_code,
                                     t.detail_code, t.error_code, t.cached, t.delivered, t.latency_ms, t.generation_id,
                                     (SELECT rating FROM feedback f WHERE f.request_id=t.request_id
                                      ORDER BY created_at DESC LIMIT 1) AS rating
                              FROM traces t {where} ORDER BY t.created_at DESC LIMIT ?""",
                          (*parameters, int(limit)))
        for row in rows:
            row["filters"] = json.loads(row["filters"])
            row["cached"], row["delivered"] = bool(row["cached"]), bool(row["delivered"])
        return rows

    def stats(self):
        rows = self._rows("""SELECT status, COUNT(*) AS n, AVG(latency_ms) AS avg_ms FROM traces
                             WHERE eval_run_id IS NULL GROUP BY status""")
        feedback = self._rows("SELECT rating, COUNT(*) AS n FROM feedback GROUP BY rating")
        latencies = [row["latency_ms"] for row in self._rows(
            "SELECT latency_ms FROM traces WHERE eval_run_id IS NULL AND latency_ms IS NOT NULL AND cached=0 "
            "ORDER BY created_at DESC LIMIT 500")]
        latencies.sort()
        pick = lambda q: latencies[min(len(latencies) - 1, round(q * (len(latencies) - 1)))] if latencies else None
        return {"by_status": rows, "feedback": feedback, "p50_ms": pick(.5), "p95_ms": pick(.95),
                "samples": len(latencies)}

    # ----- feedback / evaluation -------------------------------------------
    def add_feedback(self, request_id, *, rating, category, comment, reviewer):
        if rating not in ("good", "bad") or category not in FEEDBACK_CATEGORIES:
            raise ValueError("feedback_invalid")
        if not self._rows("SELECT 1 FROM traces WHERE request_id=?", (request_id,)):
            raise KeyError("trace_not_found")
        identifier = uuid.uuid4().hex
        with self._lock, self._connect() as database:
            database.execute("INSERT INTO feedback VALUES(?,?,?,?,?,?,?)",
                             (identifier, request_id, _now(), rating, category, comment[:4000], reviewer))
        return identifier

    def add_case(self, *, question, instrument_ids, expected_status, expected_instrument_ids=(), expected_terms=(),
                 notes="", feedback_id=None):
        if expected_status not in EXPECTED_STATUSES or not question.strip():
            raise ValueError("eval_case_invalid")
        identifier = uuid.uuid4().hex
        with self._lock, self._connect() as database:
            database.execute("INSERT INTO eval_cases VALUES(?,?,?,?,?,?,?,?,?,1)",
                             (identifier, _now(), question, _json(list(instrument_ids)), expected_status,
                              _json(list(expected_instrument_ids)), _json(list(expected_terms)), notes[:2000],
                              feedback_id))
        return identifier

    def cases(self, *, active_only=True):
        rows = self._rows("SELECT * FROM eval_cases" + (" WHERE active=1" if active_only else "") + " ORDER BY created_at")
        for row in rows:
            for key in ("instrument_ids", "expected_instrument_ids", "expected_terms"):
                row[key] = json.loads(row[key])
            row["active"] = bool(row["active"])
        return rows

    def deactivate_case(self, case_id):
        with self._lock, self._connect() as database:
            database.execute("UPDATE eval_cases SET active=0 WHERE id=?", (case_id,))

    def start_run(self, generation_id):
        identifier = uuid.uuid4().hex
        with self._lock, self._connect() as database:
            database.execute("INSERT INTO eval_runs VALUES(?,?,NULL,?,'running',NULL)", (identifier, _now(), generation_id))
        return identifier

    def record_result(self, run_id, case_id, *, request_id, status, passed, checks, latency_ms):
        with self._lock, self._connect() as database:
            database.execute("INSERT OR REPLACE INTO eval_results VALUES(?,?,?,?,?,?,?)",
                             (run_id, case_id, request_id, status, int(passed), _json(checks), latency_ms))

    def finish_run(self, run_id, summary, state="finished"):
        with self._lock, self._connect() as database:
            database.execute("UPDATE eval_runs SET finished_at=?, state=?, summary=? WHERE id=?",
                             (_now(), state, _json(summary), run_id))

    def runs(self, limit=20):
        rows = self._rows("SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT ?", (limit,))
        for row in rows:
            row["summary"] = json.loads(row["summary"]) if row["summary"] else None
        return rows

    def run_results(self, run_id):
        rows = self._rows("""SELECT r.*, c.question FROM eval_results r JOIN eval_cases c ON c.id=r.case_id
                             WHERE r.run_id=? ORDER BY r.passed, c.created_at""", (run_id,))
        for row in rows:
            row["checks"], row["passed"] = json.loads(row["checks"]), bool(row["passed"])
        return rows

    # ----- reviewer decisions ----------------------------------------------
    def save_document(self, source_id, *, reviewer, title=None, parties=None, excluded_reason=None):
        if parties is not None:
            parties = [ContractParty.model_validate(party).model_dump(mode="json") for party in parties]
            if not parties or any(not party["name"].strip() for party in parties):
                raise ValueError("parties_invalid")
        if title is not None and not title.strip():
            raise ValueError("title_invalid")
        with self._lock, self._connect() as database:
            database.execute("""INSERT INTO document_decisions VALUES(?,?,?,?,?,?)
                                ON CONFLICT(source_id) DO UPDATE SET title=excluded.title, parties=excluded.parties,
                                excluded_reason=excluded.excluded_reason, reviewer=excluded.reviewer,
                                updated_at=excluded.updated_at""",
                             (source_id, title, _json(parties) if parties is not None else None,
                              excluded_reason, reviewer, _now()))

    def clear_document(self, source_id):
        with self._lock, self._connect() as database:
            database.execute("DELETE FROM document_decisions WHERE source_id=?", (source_id,))

    def document_decision(self, source_id):
        rows = self._rows("SELECT * FROM document_decisions WHERE source_id=?", (source_id,))
        if not rows:
            return None
        row = rows[0]
        row["parties"] = json.loads(row["parties"]) if row["parties"] else None
        return row

    def save_reference(self, *, source_id, block_id, start, end, literal, kind, target_block_ids, reviewer):
        if kind not in REFERENCE_KINDS or (kind == "bind") != bool(target_block_ids):
            raise ValueError("reference_decision_invalid")
        with self._lock, self._connect() as database:
            database.execute("INSERT OR REPLACE INTO reference_decisions VALUES(?,?,?,?,?,?,?,?,?)",
                             (source_id, block_id, int(start), int(end), literal, kind,
                              _json(list(target_block_ids)), reviewer, _now()))

    def clear_reference(self, *, source_id, block_id, start, end):
        with self._lock, self._connect() as database:
            database.execute("DELETE FROM reference_decisions WHERE source_id=? AND block_id=? AND start=? AND end=?",
                             (source_id, block_id, int(start), int(end)))

    def reference_decisions(self, source_id=None):
        query = "SELECT * FROM reference_decisions" + (" WHERE source_id=?" if source_id else "")
        rows = self._rows(query, (source_id,) if source_id else ())
        for row in rows:
            row["target_block_ids"] = json.loads(row["target_block_ids"])
        return rows

    def save_relation(self, *, from_instrument_id, to_instrument_id, relation_type, affected_block_ids,
                      support_block_ids, reviewer):
        if (relation_type not in RELATION_TYPES or from_instrument_id == to_instrument_id
                or not affected_block_ids or not support_block_ids):
            raise ValueError("relation_decision_invalid")
        key = f"{from_instrument_id}|{relation_type}|{to_instrument_id}"
        with self._lock, self._connect() as database:
            database.execute("INSERT OR REPLACE INTO relation_decisions VALUES(?,?,?,?,?,?,?,?)",
                             (key, from_instrument_id, to_instrument_id, relation_type,
                              _json(list(affected_block_ids)), _json(list(support_block_ids)), reviewer, _now()))
        return key

    def delete_relation(self, relation_key):
        with self._lock, self._connect() as database:
            database.execute("DELETE FROM relation_decisions WHERE relation_key=?", (relation_key,))

    def relations(self):
        rows = self._rows("SELECT * FROM relation_decisions ORDER BY updated_at")
        for row in rows:
            row["affected_block_ids"] = json.loads(row["affected_block_ids"])
            row["support_block_ids"] = json.loads(row["support_block_ids"])
        return rows

    def pending_changes(self, since: str | None):
        """Decisions saved after the last successful publication."""
        counts = {}
        for table in ("document_decisions", "reference_decisions", "relation_decisions"):
            query = f"SELECT COUNT(*) AS n FROM {table}" + (" WHERE updated_at > ?" if since else "")
            counts[table] = self._rows(query, (since,) if since else ())[0]["n"]
        return counts

    def decisions(self) -> Decisions:
        decisions = Decisions()
        for row in self._rows("SELECT * FROM document_decisions"):
            if row["title"]:
                decisions.titles[row["source_id"]] = row["title"]
            if row["parties"]:
                decisions.parties[row["source_id"]] = [ContractParty.model_validate(p) for p in json.loads(row["parties"])]
            if row["excluded_reason"]:
                decisions.excluded[row["source_id"]] = row["excluded_reason"]
        for row in self.reference_decisions():
            decisions.references[(row["source_id"], row["block_id"], row["start"], row["end"])] = (
                row["kind"], tuple(row["target_block_ids"]))
        for row in self.relations():
            decisions.relations.append(RelationDecision(row["from_instrument_id"], row["to_instrument_id"],
                                                        row["relation_type"], tuple(row["affected_block_ids"]),
                                                        tuple(row["support_block_ids"])))
        return decisions

    # ----- publications ----------------------------------------------------
    STALE_RUNNING_HOURS = 8

    def claim_publication(self, name, reviewer):
        """Atomically create a running publication unless a recent one is running."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.STALE_RUNNING_HOURS)).isoformat()
        identifier = uuid.uuid4().hex
        with self._lock, self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            if database.execute("SELECT 1 FROM publications WHERE state='running' AND created_at > ?",
                                (cutoff,)).fetchone():
                return None
            database.execute("INSERT INTO publications VALUES(?,?,NULL,'running',?,NULL,?)",
                             (identifier, _now(), name, reviewer))
        return identifier

    def start_publication(self, name, reviewer):
        identifier = uuid.uuid4().hex
        with self._lock, self._connect() as database:
            database.execute("INSERT INTO publications VALUES(?,?,NULL,'running',?,NULL,?)",
                             (identifier, _now(), name, reviewer))
        return identifier

    def finish_publication(self, identifier, state, report):
        with self._lock, self._connect() as database:
            database.execute("UPDATE publications SET finished_at=?, state=?, report=? WHERE id=?",
                             (_now(), state, _json(report), identifier))

    def publications(self, limit=20):
        rows = self._rows("SELECT * FROM publications ORDER BY created_at DESC LIMIT ?", (limit,))
        for row in rows:
            row["report"] = json.loads(row["report"]) if row["report"] else None
        return rows

    def last_published_at(self):
        rows = self._rows("SELECT created_at FROM publications WHERE state='published' ORDER BY created_at DESC LIMIT 1")
        return rows[0]["created_at"] if rows else None
