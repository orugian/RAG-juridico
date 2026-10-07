"""Synthetic P3B ledger contracts: scoped current decisions, never real approvals."""
import csv
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

import pytest

from app.ingestion.review_store import ReviewRecord, ReviewStore


IDENTITY = dict(source_id="synthetic-source", snapshot="synthetic-snapshot",
                file_hash="a" * 64, configuration_version="synthetic-v1")
REVIEW = dict(decision="approved", reviewer="synthetic-fixture", reason="Synthetic test only")


def approve(store, **kwargs):
    return store.record(**(IDENTITY | REVIEW | kwargs))


def current(store, record, **kwargs):
    return store.require_current(record_id=record.record_id, **(IDENTITY | kwargs))


@pytest.mark.parametrize("scope", ["unit", "relation", "family"])
def test_scoped_decisions_are_independent_and_require_exact_subject_digest(tmp_path, scope):
    store = ReviewStore(tmp_path / "synthetic.sqlite3")
    source = approve(store)
    subject = dict(scope=scope, subject_id="subject-a", subject_digest="b" * 64)
    first = approve(store, **subject)
    second = approve(store, **(subject | {"subject_id": "subject-b"}))
    assert current(store, source) == source
    assert current(store, first, **subject) == first
    assert second.supersedes_record_id is None
    assert store.latest(**(IDENTITY | subject)).record_id == first.record_id
    assert store.latest(**(IDENTITY | subject | {"subject_digest": "c" * 64})) is None
    with pytest.raises(ValueError):
        current(store, source, **subject)
    with pytest.raises(ValueError):
        current(store, first, **(subject | {"subject_id": "subject-b"}))


def test_parties_review_is_bound_to_source_and_does_not_supersede_source(tmp_path):
    store = ReviewStore(tmp_path / "synthetic.sqlite3")
    source = approve(store)
    subject = dict(scope="parties", subject_id=IDENTITY["source_id"], subject_digest="b" * 64)
    parties = approve(store, **subject)
    assert parties.supersedes_record_id is None
    assert current(store, source) == source
    assert current(store, parties, **subject) == parties
    with pytest.raises(ValueError):
        current(store, source, **subject)
    with pytest.raises(ValueError):
        approve(store, **(subject | {"subject_id": "other-source"}))


@pytest.mark.parametrize("bad", [{"record_id": None}, {"record_id": "invented"},
    {"source_id": "other"}, {"snapshot": "changed"}, {"file_hash": "c" * 64},
    {"configuration_version": "changed"}])
def test_current_rejects_false_missing_and_mismatched_identity(tmp_path, bad):
    store = ReviewStore(tmp_path / "synthetic.sqlite3")
    record = approve(store)
    with pytest.raises(ValueError):
        store.require_current(**(IDENTITY | {"record_id": record.record_id} | bad))


@pytest.mark.parametrize("decision", ["approved", "quarantined", "excluded"])
def test_superseded_and_revoked_records_cannot_authorize(tmp_path, decision):
    store = ReviewStore(tmp_path / "synthetic.sqlite3")
    subject = dict(scope="unit", subject_id="unit-a", subject_digest="b" * 64)
    first = approve(store, **subject)
    replacement = approve(store, **(subject | {"decision": decision}))
    assert replacement.supersedes_record_id == first.record_id
    with pytest.raises(ValueError):
        current(store, first, **subject)
    if decision == "approved":
        assert current(store, replacement, **subject) == replacement
    else:
        with pytest.raises(ValueError):
            current(store, replacement, **subject)


def test_changed_subject_content_invalidates_old_approval_and_keeps_append_only(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    store = ReviewStore(path)
    subject = dict(scope="unit", subject_id="unit-a", subject_digest="b" * 64)
    first = approve(store, **subject)
    changed = subject | {"subject_digest": "c" * 64}
    replacement = approve(store, **changed)
    assert replacement.supersedes_record_id == first.record_id
    assert store.latest(**(IDENTITY | subject)) is None
    with sqlite3.connect(path) as db:
        for statement in ("UPDATE reviews SET payload='{}'", "DELETE FROM reviews"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(statement)
    assert store.history(IDENTITY["source_id"]) == [first, replacement]


@pytest.mark.parametrize("bad", [dict(scope="unit"), dict(scope="unit", subject_id="unit-a"),
    dict(scope="unit", subject_id="", subject_digest="b" * 64),
    dict(scope="unit", subject_id="unit-a", subject_digest="bad"),
    dict(scope="source", subject_digest="b" * 64),
    dict(scope="source", subject_id="other"), dict(scope="unknown")])
def test_records_reject_incomplete_or_inappropriately_scoped_subjects(tmp_path, bad):
    store = ReviewStore(tmp_path / "synthetic.sqlite3")
    with pytest.raises(ValueError):
        approve(store, **bad)
    assert store.history(IDENTITY["source_id"]) == []


def test_explicit_source_subject_cannot_create_independent_source_approval(tmp_path):
    store = ReviewStore(tmp_path / "synthetic.sqlite3")
    first = approve(store)
    revoked = approve(store, subject_id=IDENTITY["source_id"], decision="quarantined")
    assert revoked.subject_id is None
    assert revoked.supersedes_record_id == first.record_id
    with pytest.raises(ValueError):
        current(store, first)


def test_canonical_digest_is_order_stable_unicode_exact_and_rejects_nan():
    from app.ingestion.review_store import review_digest
    value = {"texto": "não ✨", "nested": {"b": 2, "a": 1}}
    expected = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    assert review_digest(value) == expected
    assert review_digest(value) == review_digest({"nested": {"a": 1, "b": 2}, "texto": "não ✨"})
    assert review_digest([1, 2]) != review_digest([2, 1])
    with pytest.raises(ValueError):
        review_digest({"not_json": float("nan")})


def test_unit_digest_covers_every_field_except_mutable_decision():
    from app.contracts import CitationUnit
    from app.ingestion.review_store import review_digest, unit_review_digest
    unit = CitationUnit(unit_id="u", instrument_id="i", verbatim_text="Não pagar, salvo condição.",
        source_ids=["s"], block_ids=["b"], spans=[dict(source_id="s", block_id="b", start=0, end=27)],
        location=dict(label="Cláusula sintética"), contract_title="Contrato sintético",
        synthetic_context="Numeração reconstruída", parent_unit_id="parent")
    payload = unit.model_dump(mode="json", exclude={"approval_state", "review_record_id"})
    assert unit_review_digest(unit) == review_digest(payload)
    assert unit_review_digest(unit.model_copy(update={"approval_state": "approved", "review_record_id": "r"})) == unit_review_digest(unit)
    mutations = dict(verbatim_text="Pagar, salvo condição.", contract_title="Outro título",
        parties=[dict(name="Parte sintética")], synthetic_context="Outra numeração",
        parent_unit_id="other", closure_unit_ids=["dependency"], source_identities=[dict(source_id="s",
        instrument_id="i", doc_version=2, file_id="f", file_hash="a" * 64, snapshot="snap",
        configuration_version="v", parser_name="docx", parser_version="1")])
    for field, value in mutations.items():
        changed = CitationUnit.model_validate(unit.model_dump() | {field: value})
        assert unit_review_digest(changed) != unit_review_digest(unit), field


def legacy_record():
    return dict(record_id="legacy-synthetic", **IDENTITY, **REVIEW,
                recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(), supersedes_record_id=None)


def test_migration_preserves_legacy_payload_and_sequence_without_updates(tmp_path):
    path = tmp_path / "legacy-synthetic.sqlite3"
    payload = json.dumps(legacy_record())
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE reviews (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id TEXT NOT NULL UNIQUE, source_id TEXT NOT NULL, snapshot TEXT NOT NULL,
                file_hash TEXT NOT NULL, configuration_version TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TRIGGER no_update BEFORE UPDATE ON reviews BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
            CREATE TRIGGER no_delete BEFORE DELETE ON reviews BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END;
        """)
        db.execute("INSERT INTO reviews(record_id,source_id,snapshot,file_hash,configuration_version,payload) VALUES(?,?,?,?,?,?)",
            ("legacy-synthetic", *IDENTITY.values(), payload))
    store = ReviewStore(path)
    legacy = store.latest(**IDENTITY)
    assert legacy.scope == "source" and legacy.subject_id is None
    assert current(store, legacy) == legacy
    approve(store, scope="unit", subject_id="unit-a", subject_digest="b" * 64)
    assert current(store, legacy) == legacy
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT sequence,payload FROM reviews WHERE record_id='legacy-synthetic'").fetchone() == (1, payload)


def test_csv_scoped_roundtrip_is_formula_safe_and_old_v2_is_source_only(tmp_path):
    source = ReviewStore(tmp_path / "source.sqlite3")
    first = approve(source)
    second = approve(source, scope="unit", subject_id="=synthetic", subject_digest="b" * 64, reason="@synthetic")
    path = tmp_path / "reviews.csv"
    source.export_csv(path)
    assert "'=synthetic" in path.read_text(encoding="utf-8")
    target = ReviewStore(tmp_path / "target.sqlite3")
    target.import_csv(path)
    target.import_csv(path)
    assert target.history(IDENTITY["source_id"]) == [first, second]
    legacy_path = tmp_path / "legacy-v2.csv"
    values = legacy_record()
    with legacy_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["_csv_encoding", *values])
        writer.writeheader()
        writer.writerow({"_csv_encoding": "review-quote-v2", **values})
    legacy_target = ReviewStore(tmp_path / "legacy-target.sqlite3")
    legacy_target.import_csv(legacy_path)
    assert legacy_target.latest(**IDENTITY).scope == "source"
    with pytest.raises(ValueError):
        legacy_target.require_current(record_id=values["record_id"], **IDENTITY,
            scope="unit", subject_id="unit-a", subject_digest="b" * 64)


def test_import_rejects_cross_scope_supersession_atomically(tmp_path):
    source = ReviewStore(tmp_path / "source.sqlite3")
    first = approve(source)
    approve(source, scope="unit", subject_id="unit-a", subject_digest="b" * 64)
    path = tmp_path / "reviews.csv"
    source.export_csv(path)
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    rows[1]["supersedes_record_id"] = first.record_id
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    target = ReviewStore(tmp_path / "target.sqlite3")
    with pytest.raises(ValueError, match="histórico"):
        target.import_csv(path)
    assert target.history(IDENTITY["source_id"]) == []


def test_import_rejects_ambiguous_duplicate_headers(tmp_path):
    values = legacy_record()
    path = tmp_path / "ambiguous.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["_csv_encoding", *values, "scope", "scope"])
        writer.writerow(["review-quote-v2", *values.values(), "unit", "source"])
    target = ReviewStore(tmp_path / "target.sqlite3")
    with pytest.raises(ValueError):
        target.import_csv(path)
    assert target.history(IDENTITY["source_id"]) == []


def test_column_payload_identity_mismatch_never_approves(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    store = ReviewStore(path)
    source = approve(store)
    wrong = ReviewRecord.model_validate(source.model_dump() | {"record_id": "wrong-synthetic",
        "source_id": "other-synthetic"})
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO reviews(record_id,source_id,snapshot,file_hash,configuration_version,payload) VALUES(?,?,?,?,?,?)",
            (wrong.record_id, *IDENTITY.values(), wrong.model_dump_json()))
    assert store.latest(**IDENTITY) is None
    with pytest.raises(ValueError):
        current(store, wrong)


@pytest.mark.parametrize("scope", ["source", "parties", "unit", "relation", "family"])
def test_state_digest_changes_on_each_scoped_revision_or_revocation(tmp_path, scope):
    from app.ingestion.review_store import review_digest
    store = ReviewStore(tmp_path / "synthetic.sqlite3")
    empty = store.state_digest()
    assert empty == review_digest([])
    subject = {} if scope == "source" else dict(scope=scope,
        subject_id=IDENTITY["source_id"] if scope == "parties" else "subject-a", subject_digest="b" * 64)
    first = approve(store, **subject)
    approved = store.state_digest()
    assert approved != empty
    revoked = approve(store, **(subject | {"decision": "quarantined"}))
    assert store.state_digest() != approved
    assert store.state_digest() == review_digest([revoked.model_dump(mode="json")])
    assert first.record_id != revoked.record_id


def test_state_digest_is_sorted_current_only_and_unaffected_by_reads(tmp_path):
    from app.ingestion.review_store import review_digest
    path = tmp_path / "synthetic.sqlite3"
    store = ReviewStore(path)
    unit = approve(store, scope="unit", subject_id="u", subject_digest="b" * 64)
    source = approve(store)
    unit = approve(store, scope="unit", subject_id="u", subject_digest="c" * 64)
    family = approve(store, scope="family", subject_id="f", subject_digest="d" * 64)
    state = store.state_digest()
    heads = sorted([source, unit, family], key=lambda item: item.ledger_key)
    assert state == review_digest([head.model_dump(mode="json") for head in heads])
    store.latest(**IDENTITY)
    store.history(IDENTITY["source_id"])
    csv_path = tmp_path / "reviews.csv"
    store.export_csv(csv_path)
    store.import_csv(csv_path)  # Idempotent import writes no record.
    assert store.state_digest() == state
    assert ReviewStore(path).state_digest() == state


@pytest.mark.parametrize("column, replacement", [("source_id", "other"), ("record_id", "other"),
    ("snapshot", "other"), ("file_hash", "c" * 64), ("configuration_version", "other"),
    ("scope", "relation"), ("subject_id", "other")])
def test_state_digest_rejects_head_index_payload_identity_mismatch(tmp_path, column, replacement):
    path = tmp_path / "synthetic.sqlite3"
    store = ReviewStore(path)
    original = approve(store)
    # Simulate malformed direct insertion; no mutation of historical payloads.
    columns = dict(record_id="bad-synthetic", **IDENTITY, scope="source", subject_id="")
    payload = original.model_dump() | {"record_id": "bad-synthetic"}
    columns[column] = replacement
    values = (*columns.values(), ReviewRecord.model_validate(payload).model_dump_json())
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO reviews(record_id,source_id,snapshot,file_hash,configuration_version,scope,subject_id,payload) VALUES(?,?,?,?,?,?,?,?)", values)
    with pytest.raises(ValueError):
        store.state_digest()


def test_relation_state_digest_changes_for_new_revised_or_revoked_relation(tmp_path):
    from app.ingestion.review_store import review_digest
    store = ReviewStore(tmp_path / "synthetic.sqlite3")
    empty = store.relation_state_digest()
    assert empty == review_digest([])
    subject = dict(scope="relation", subject_id="relation-a", subject_digest="b" * 64)
    original = approve(store, **subject)
    approved = store.relation_state_digest()
    assert approved != empty
    assert approved == review_digest([original.model_dump(mode="json")])
    replacement = approve(store, **(subject | {"subject_digest": "c" * 64}))
    revised = store.relation_state_digest()
    assert revised != approved
    assert revised == review_digest([replacement.model_dump(mode="json")])
    revoked = approve(store, **(subject | {"subject_digest": "c" * 64, "decision": "quarantined"}))
    invalidated = store.relation_state_digest()
    assert invalidated != revised
    assert invalidated == review_digest([revoked.model_dump(mode="json")])
    another = approve(store, **(subject | {"subject_id": "relation-0"}))
    assert store.relation_state_digest() == review_digest([
        another.model_dump(mode="json"), revoked.model_dump(mode="json")])


@pytest.mark.parametrize("scope", ["source", "parties", "unit", "family"])
def test_relation_state_digest_is_unaffected_by_other_scope_writes(tmp_path, scope):
    store = ReviewStore(tmp_path / "synthetic.sqlite3")
    approve(store, scope="relation", subject_id="relation-a", subject_digest="b" * 64)
    registry = store.relation_state_digest()
    global_state = store.state_digest()
    subject = {} if scope == "source" else dict(scope=scope,
        subject_id=IDENTITY["source_id"] if scope == "parties" else "subject-a", subject_digest="b" * 64)
    approve(store, **subject)
    assert store.state_digest() != global_state
    assert store.relation_state_digest() == registry
    approve(store, **(subject | {"decision": "excluded"}))
    assert store.relation_state_digest() == registry


def test_relation_state_digest_rejects_current_relation_head_payload_mismatch(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    store = ReviewStore(path)
    record = approve(store, scope="relation", subject_id="relation-a", subject_digest="b" * 64)
    payload = record.model_dump() | {"record_id": "bad-synthetic", "subject_id": "relation-b"}
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO reviews(record_id,source_id,snapshot,file_hash,configuration_version,scope,subject_id,payload) VALUES(?,?,?,?,?,?,?,?)",
            ("bad-synthetic", *IDENTITY.values(), "relation", "relation-a", ReviewRecord.model_validate(payload).model_dump_json()))
    with pytest.raises(ValueError):
        store.relation_state_digest()
