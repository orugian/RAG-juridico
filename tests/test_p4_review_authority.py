"""P4 authority loss/tamper regressions using only synthetic SQLite ledgers."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.ingestion.review_store import ReviewStore
from app.retrieval.review_authority import ReadOnlyReviewAuthority


@pytest.fixture
def authority_fixture(tmp_path):
    path = tmp_path / "authority.sqlite"
    writer = ReviewStore(path)
    base = dict(source_id="synthetic-source", snapshot="synthetic-snapshot", file_hash="a" * 64,
        configuration_version="synthetic-cfg", decision="approved", reviewer="synthetic reviewer",
        reason="synthetic fixture; no human approval")
    records = [writer.record(**base)]
    for scope, subject in (("parties", base["source_id"]), ("unit", "u1"), ("relation", "r1"), ("family", "f1")):
        records.append(writer.record(**base, scope=scope, subject_id=subject, subject_digest="b" * 64))
    identity = {key: base[key] for key in ("source_id", "snapshot", "file_hash", "configuration_version")}
    return path, writer, identity, records


def test_authority_constructor_never_bootstraps_base_store(authority_fixture, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("read authority bootstrapped/migrated ReviewStore")
    monkeypatch.setattr(ReviewStore, "__init__", forbidden)
    authority = ReadOnlyReviewAuthority(authority_fixture[0])
    assert authority.state_digest()


def test_existing_ledger_bytes_and_schema_preserved(authority_fixture):
    path, writer, identity, records = authority_fixture
    before = path.read_bytes()
    authority = ReadOnlyReviewAuthority(path)
    assert authority.history(identity["source_id"]) == writer.history(identity["source_id"])
    assert authority.state_digest() == writer.state_digest()
    assert authority.relation_state_digest() == writer.relation_state_digest()
    assert path.read_bytes() == before
    assert authority.path == path.absolute()


@pytest.mark.parametrize("method", ["latest", "require_current", "history", "state_digest", "relation_state_digest"])
def test_live_loss_fails_controlled_without_recreating_database(authority_fixture, method):
    path, _, identity, records = authority_fixture
    authority = ReadOnlyReviewAuthority(path)
    path.rename(path.with_suffix(".retained"))
    call = getattr(authority, method)
    kwargs = identity if method in ("latest", "require_current") else {}
    if method == "require_current":
        kwargs = dict(kwargs, record_id=records[0].record_id)
    args = (identity["source_id"],) if method == "history" else ()
    with pytest.raises(ValueError, match="review_authority_unavailable"):
        call(*args, **kwargs)
    assert not path.exists()


def test_absent_constructor_does_not_create_database_or_parent(tmp_path):
    path = tmp_path / "absent-parent" / "authority.sqlite"
    with pytest.raises(ValueError, match="review_authority_unavailable"):
        ReadOnlyReviewAuthority(path)
    assert not path.parent.exists()


def test_new_process_does_not_bootstrap_missing_authority(tmp_path):
    path = tmp_path / "absent" / "authority.sqlite"
    code = """from pathlib import Path
import sys
from app.retrieval.review_authority import ReadOnlyReviewAuthority
try:
    ReadOnlyReviewAuthority(Path(sys.argv[1]))
except ValueError as exc:
    assert str(exc) == 'review_authority_unavailable'
    raise SystemExit(0)
raise SystemExit(9)
"""
    result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert not path.parent.exists()


@pytest.mark.parametrize("scope,index", [("source", 0), ("parties", 1), ("unit", 2), ("relation", 3), ("family", 4)])
def test_scoped_current_heads_and_exact_identity(authority_fixture, scope, index):
    path, writer, identity, records = authority_fixture
    authority = ReadOnlyReviewAuthority(path)
    record = records[index]
    subject = dict(scope=scope, subject_id=record.subject_id, subject_digest=record.subject_digest)
    assert authority.latest(**identity, **subject) == record
    assert authority.require_current(**identity, **subject, record_id=record.record_id) == record
    for field, value in (("snapshot", "other"), ("file_hash", "c" * 64), ("configuration_version", "other")):
        assert authority.latest(**(identity | {field: value}), **subject) is None
    new = writer.record(**identity, **subject, decision="quarantined", reviewer="synthetic", reason="revocation fixture")
    assert authority.latest(**identity, **subject) == new
    with pytest.raises(ValueError):
        authority.require_current(**identity, **subject, record_id=record.record_id)
    with pytest.raises(ValueError):
        authority.require_current(**identity, **subject, record_id=new.record_id)


@pytest.mark.parametrize("method", ["record", "import_csv", "export_csv"])
def test_mutating_interfaces_explicitly_refused(authority_fixture, method, tmp_path):
    path, _, identity, _ = authority_fixture
    authority = ReadOnlyReviewAuthority(path)
    before = path.read_bytes()
    target = tmp_path / "must-not-exist.csv"
    with pytest.raises(ValueError, match="review_authority_read_only"):
        if method == "record":
            authority.record(**identity, decision="approved", reviewer="synthetic", reason="forbidden")
        else:
            getattr(authority, method)(target)
    assert not target.exists()
    assert path.read_bytes() == before


def test_replacement_database_rejected(authority_fixture, tmp_path):
    path, _, identity, _ = authority_fixture
    authority = ReadOnlyReviewAuthority(path)
    other = tmp_path / "other.sqlite"
    ReviewStore(other)
    path.rename(tmp_path / "retained.sqlite")
    other.rename(path)
    with pytest.raises(ValueError, match="review_authority_invalid"):
        authority.latest(**identity)


@pytest.mark.parametrize("payload", [b"", b"not a SQLite database"])
def test_corrupt_database_controlled_at_constructor(tmp_path, payload):
    path = tmp_path / "authority.sqlite"
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="review_authority_invalid"):
        ReadOnlyReviewAuthority(path)
    assert path.read_bytes() == payload


def test_missing_table_schema_controlled(tmp_path):
    path = tmp_path / "authority.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="review_authority_invalid"):
        ReadOnlyReviewAuthority(path)
    assert path.read_bytes() == before


def test_live_schema_corruption_fails_controlled(authority_fixture):
    path, _, _, _ = authority_fixture
    authority = ReadOnlyReviewAuthority(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE reviews")
    with pytest.raises(ValueError, match="review_authority_invalid"):
        authority.state_digest()


def test_sqlite_connection_is_read_only_query_only(authority_fixture):
    authority = ReadOnlyReviewAuthority(authority_fixture[0])
    with authority._connect() as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden (id INTEGER)")


def test_junction_or_symlink_authority_directory_rejected(authority_fixture, tmp_path):
    path = authority_fixture[0]
    alias = tmp_path / "alias"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(path.parent)],
            capture_output=True, check=False)
        assert result.returncode == 0
    else:
        alias.symlink_to(path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="review_authority_invalid"):
        ReadOnlyReviewAuthority(alias / path.name)


def test_active_wal_updates_visible_without_cached_approval(authority_fixture):
    path, writer, identity, records = authority_fixture
    with sqlite3.connect(path) as keepalive:
        keepalive.execute("PRAGMA journal_mode=WAL")
        authority = ReadOnlyReviewAuthority(path)
        before = authority.state_digest()
        assert authority.latest(**identity) == records[0]
        revoked = writer.record(**identity, decision="quarantined", reviewer="synthetic", reason="WAL live revocation")
        assert Path(str(path) + "-wal").stat().st_size > 0
        assert authority.latest(**identity) == revoked
        assert authority.state_digest() != before


def test_hold_current_never_changes_ledger_bytes_or_approvals(authority_fixture):
    path, writer, identity, records = authority_fixture
    authority = ReadOnlyReviewAuthority(path)
    before = path.read_bytes()
    digest = authority.state_digest()
    with authority.hold_current() as lease:
        assert lease is None
        assert authority.latest(**identity) == records[0]
        assert authority.state_digest() == digest
    assert path.read_bytes() == before
    assert authority.state_digest() == digest == writer.state_digest()


def test_hold_current_blocks_real_writer_until_boundary_finishes(authority_fixture):
    path, writer, identity, records = authority_fixture
    authority = ReadOnlyReviewAuthority(path)
    started = Event()
    finished = Event()
    def revoke():
        started.set()
        result = writer.record(**identity, decision="quarantined", reviewer="synthetic", reason="concurrent lease fixture")
        finished.set()
        return result
    with ThreadPoolExecutor(max_workers=1) as executor:
        with authority.hold_current():
            with sqlite3.connect(path, timeout=0.01) as competitor:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    competitor.execute("BEGIN IMMEDIATE")
            future = executor.submit(revoke)
            assert started.wait(2)
            assert not finished.wait(0.15)
            assert authority.latest(**identity) == records[0]
        record = future.result(timeout=5)
        assert authority.latest(**identity) == record


def test_hold_current_query_only_blocks_any_sql_write(authority_fixture, monkeypatch):
    authority = ReadOnlyReviewAuthority(authority_fixture[0])
    original_connect = sqlite3.connect
    connections = []
    def capture(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, "connect", capture)
    with authority.hold_current():
        assert len(connections) == 1
        connection = connections[0]
        assert connection.in_transaction
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden (id INTEGER)")


def test_hold_current_loss_does_not_create_new_ledger(authority_fixture):
    path = authority_fixture[0]
    authority = ReadOnlyReviewAuthority(path)
    path.rename(path.with_suffix(".retained"))
    with pytest.raises(ValueError, match="review_authority_unavailable"):
        with authority.hold_current():
            pytest.fail("lease acquired with missing ledger")
    assert not path.exists()


def test_hold_current_always_releases_lock_on_callback_failure(authority_fixture):
    path, writer, identity, _ = authority_fixture
    authority = ReadOnlyReviewAuthority(path)
    with pytest.raises(RuntimeError, match="callback failure"):
        with authority.hold_current():
            raise RuntimeError("callback failure")
    revoked = writer.record(**identity, decision="quarantined", reviewer="synthetic", reason="post failure fixture")
    assert authority.latest(**identity) == revoked


@pytest.mark.parametrize("method", ["latest", "hold_current"])
def test_loss_between_guard_and_sqlite_open_never_creates_database(authority_fixture, monkeypatch, method):
    path, _, identity, _ = authority_fixture
    authority = ReadOnlyReviewAuthority(path)
    original = sqlite3.connect
    def lose_then_open(*args, **kwargs):
        assert kwargs.get("uri") is True
        assert "mode=ro" in args[0] if method == "latest" else "mode=rw" in args[0]
        path.rename(path.with_suffix(".retained"))
        return original(*args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", lose_then_open)
    with pytest.raises(ValueError, match="review_authority_unavailable"):
        if method == "latest":
            authority.latest(**identity)
        else:
            with authority.hold_current():
                pytest.fail("missing DB admitted")
    assert not path.exists()


def test_exclusive_sqlite_busy_read_is_controlled(authority_fixture):
    path, _, identity, _ = authority_fixture
    # Delete-mode exclusive lock provides a real blocked read, not a mock error.
    with sqlite3.connect(path) as setup:
        assert setup.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    authority = ReadOnlyReviewAuthority(path)
    with sqlite3.connect(path) as exclusive:
        exclusive.execute("BEGIN EXCLUSIVE")
        with pytest.raises(ValueError, match="review_authority_unavailable"):
            authority.latest(**identity)
        exclusive.rollback()
    assert authority.latest(**identity) is not None


def test_competing_publication_lease_is_controlled(authority_fixture):
    path = authority_fixture[0]
    first = ReadOnlyReviewAuthority(path)
    second = ReadOnlyReviewAuthority(path)
    with first.hold_current():
        with pytest.raises(ValueError, match="review_authority_unavailable"):
            with second.hold_current():
                pytest.fail("second reserved transaction admitted")


@pytest.mark.parametrize("method", ["latest", "history", "state_digest", "relation_state_digest"])
def test_invalid_payload_becomes_controlled_authority_failure(authority_fixture, method):
    path, _, identity, _ = authority_fixture
    authority = ReadOnlyReviewAuthority(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER no_update")
        connection.execute("UPDATE reviews SET payload='not JSON'")
    with pytest.raises(ValueError, match="review_authority_invalid"):
        if method == "latest":
            authority.latest(**identity)
        elif method == "history":
            authority.history(identity["source_id"])
        else:
            getattr(authority, method)()


def test_sql_columns_payload_divergence_is_controlled(authority_fixture):
    path = authority_fixture[0]
    authority = ReadOnlyReviewAuthority(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER no_update")
        connection.execute("UPDATE reviews SET file_hash=?", ("c" * 64,))
    with pytest.raises(ValueError, match="review_authority_invalid"):
        authority.state_digest()


def test_header_corruption_of_live_authority_is_controlled(authority_fixture):
    path = authority_fixture[0]
    authority = ReadOnlyReviewAuthority(path)
    with path.open("r+b") as handle:
        handle.write(b"not SQLite file")
    with pytest.raises(ValueError, match="review_authority_invalid"):
        authority.state_digest()


def test_connection_trace_contains_no_schema_or_journal_writes(authority_fixture, monkeypatch):
    statements = []
    original = sqlite3.connect
    def capture(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(sqlite3, "connect", capture)
    authority = ReadOnlyReviewAuthority(authority_fixture[0])
    authority.state_digest()
    with authority.hold_current():
        authority.relation_state_digest()
    assert not any(statement.lstrip().upper().startswith(("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE", "DROP"))
                   for statement in statements)
    assert not any("JOURNAL_MODE" in statement.upper() for statement in statements)


def test_hold_current_blocks_writer_in_independent_process(authority_fixture):
    path, _, identity, records = authority_fixture
    authority = ReadOnlyReviewAuthority(path)
    script = """from pathlib import Path
import sys
from app.ingestion.review_store import ReviewStore
writer = ReviewStore(Path(sys.argv[1]))
print('ready', flush=True)
sys.stdin.read(1)
print('started', flush=True)
writer.record(source_id='synthetic-source',snapshot='synthetic-snapshot',file_hash='a'*64,
configuration_version='synthetic-cfg',decision='quarantined',reviewer='synthetic',reason='external process fixture')
print('committed', flush=True)
"""
    child = subprocess.Popen([sys.executable, "-u", "-c", script, str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with authority.hold_current():
            child.stdin.write("1")
            child.stdin.flush()
            assert child.stdout.readline().strip() == "started"
            assert child.poll() is None
            assert authority.latest(**identity) == records[0]
        output, errors = child.communicate(timeout=10)
        assert child.returncode == 0, errors
        assert output.strip() == "committed"
        assert authority.latest(**identity).decision == "quarantined"
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_sqlite_cleanup_error_at_constructor_is_controlled(authority_fixture, monkeypatch):
    original = sqlite3.connect
    class CleanupFailure(sqlite3.Connection):
        def close(self):
            super().close()
            raise sqlite3.OperationalError("synthetic cleanup failure")
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: original(*args, **kwargs, factory=CleanupFailure))
    with pytest.raises(ValueError, match="review_authority_invalid"):
        ReadOnlyReviewAuthority(authority_fixture[0])
