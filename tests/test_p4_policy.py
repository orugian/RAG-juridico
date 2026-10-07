"""Local synthetic policy authority: no corpus, provider, or AWS durability claims."""
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Event

import pytest


def _journal(path, **kwargs):
    from app.retrieval.policy import SQLitePolicyJournal
    return SQLitePolicyJournal(path, **kwargs)


def test_missing_authority_never_bootstraps_implicitly(tmp_path):
    path = tmp_path / "absent" / "policy.sqlite"
    with pytest.raises(ValueError, match="policy_authority"):
        _journal(path)
    assert not path.parent.exists()


def test_explicit_create_and_reopen_bind_identity(tmp_path):
    path = tmp_path / "authority" / "policy.sqlite"
    journal = _journal(path, initialize=True, journal_id="synthetic-office")
    before = journal.snapshot()
    assert before.policy_epoch == 0
    assert before.journal_id == "synthetic-office"
    assert _journal(path, journal_id="synthetic-office").snapshot() == before
    with pytest.raises(ValueError, match="identity"):
        _journal(path, journal_id="another-office")
    with pytest.raises(ValueError, match="already_exists"):
        _journal(path, initialize=True)


@pytest.mark.parametrize("scope,field", [("source", "blocked_source_ids"),
    ("credential", "blocked_credential_ids"), ("family", "blocked_family_ids")])
def test_committed_block_is_live_across_instances(tmp_path, scope, field):
    path = tmp_path / "policy.sqlite"
    writer = _journal(path, initialize=True)
    reader = _journal(path)
    old = reader.snapshot()
    committed = writer.block(scope, "synthetic-identifier", "review_changed")
    assert committed.policy_epoch == 1
    assert committed.digest != old.digest
    assert getattr(reader.snapshot(), field) == ["synthetic-identifier"]
    assert reader.snapshot() == committed
    repeated = writer.block(scope, "synthetic-identifier", "confirmed_again")
    assert repeated.policy_epoch == 2
    assert getattr(repeated, field) == ["synthetic-identifier"]


@pytest.mark.parametrize("values", [("invalid", "s1", "changed"), ("source", "", "changed"),
    ("source", "  ", "changed"), ("source", "s1", ""), ("source", "s1", "has spaces"),
    ("source", 1, "changed"), ("source", "s\x00", "changed")])
def test_invalid_block_never_changes_epoch(tmp_path, values):
    journal = _journal(tmp_path / "policy.sqlite", initialize=True)
    with pytest.raises(ValueError):
        journal.block(*values)
    assert journal.snapshot().policy_epoch == 0


@pytest.mark.parametrize("artifact", ["database", "checkpoint", "lock"])
def test_authority_artifact_loss_fails_closed_and_cannot_reinitialize(tmp_path, artifact):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    journal.block("source", "s1", "changed")
    target = {"database": path, "checkpoint": path.with_name(path.name + ".checkpoint.json"),
              "lock": path.with_name(path.name + ".lock")}[artifact]
    target.unlink()
    with pytest.raises(ValueError, match="policy_authority"):
        journal.snapshot()
    with pytest.raises(ValueError, match="policy_authority|already_exists"):
        _journal(path)
    with pytest.raises(ValueError, match="already_exists"):
        _journal(path, initialize=True)
    assert not target.exists()


def test_old_database_restore_does_not_restore_access(tmp_path):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    old = path.read_bytes()
    journal.block("source", "s1", "removed")
    path.write_bytes(old)
    with pytest.raises(ValueError, match="policy_authority"):
        journal.snapshot()
    with pytest.raises(ValueError, match="policy_authority"):
        _journal(path)


def test_live_authority_detects_full_rollback_even_with_matching_old_checkpoint(tmp_path):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    checkpoint = path.with_name(path.name + ".checkpoint.json")
    old_database, old_checkpoint = path.read_bytes(), checkpoint.read_bytes()
    journal.block("source", "s1", "removed")
    path.write_bytes(old_database)
    checkpoint.write_bytes(old_checkpoint)
    with pytest.raises(ValueError, match="policy_authority.*rollback"):
        journal.snapshot()


def test_checkpoint_epoch_bool_cannot_impersonate_zero(tmp_path):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    checkpoint = path.with_name(path.name + ".checkpoint.json")
    checkpoint.write_bytes(checkpoint.read_bytes().replace(b'"policy_epoch":0', b'"policy_epoch":false'))
    with pytest.raises(ValueError, match="policy_authority"):
        journal.snapshot()


def test_replaced_database_cannot_impersonate_live_file_identity(tmp_path):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    replacement = tmp_path / "replacement.sqlite"
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(ValueError, match="policy_authority_identity_changed"):
        journal.snapshot()


@pytest.mark.parametrize("mutation", ["payload", "head", "trigger", "checkpoint", "garbage"])
def test_chain_head_and_checkpoint_corruption_fails_closed(tmp_path, mutation):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    journal.block("source", "s1", "removed")
    if mutation == "checkpoint":
        path.with_name(path.name + ".checkpoint.json").write_text("{}", encoding="utf-8")
    elif mutation == "garbage":
        path.write_bytes(b"corrupted authority")
    else:
        with sqlite3.connect(path) as database:
            if mutation == "payload":
                database.execute("DROP TRIGGER policy_events_no_update")
                database.execute("UPDATE policy_events SET payload='{}' WHERE epoch=1")
            elif mutation == "head":
                database.execute("UPDATE policy_head SET epoch=0")
            else:
                database.execute("DROP TRIGGER policy_events_no_delete")
    with pytest.raises(ValueError, match="policy_authority"):
        journal.snapshot()


def test_failed_persistence_never_acknowledges_block(tmp_path):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    with sqlite3.connect(path) as database:
        database.execute("CREATE TRIGGER simulate_full BEFORE INSERT ON policy_events "
                         "BEGIN SELECT RAISE(ABORT, 'synthetic storage full'); END")
    with pytest.raises(ValueError, match="policy_authority"):
        journal.block("credential", "credential-1", "revoked")
    assert journal.snapshot().policy_epoch == 0


def test_checkpoint_failure_after_commit_blocks_until_explicit_recovery(tmp_path, monkeypatch):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    def failure(*args):
        raise OSError("synthetic checkpoint failure")
    monkeypatch.setattr(journal, "_write_checkpoint", failure)
    with pytest.raises(ValueError, match="policy_authority"):
        journal.block("source", "s1", "removed")
    with pytest.raises(ValueError, match="policy_authority"):
        journal.snapshot()
    with pytest.raises(ValueError, match="policy_authority"):
        _journal(path)


def test_sql_append_only_is_enforced(tmp_path):
    path = tmp_path / "policy.sqlite"
    _journal(path, initialize=True).block("source", "s1", "removed")
    with sqlite3.connect(path) as database:
        for statement in ("DELETE FROM policy_events", "UPDATE policy_events SET payload='{}'",
                          "DELETE FROM policy_identity", "UPDATE policy_identity SET journal_id='x'"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                database.execute(statement)


def test_thread_writers_have_contiguous_committed_epochs(tmp_path):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    with ThreadPoolExecutor(max_workers=6) as workers:
        epochs = list(workers.map(lambda i: _journal(path).block("source", f"s{i}", "removed").policy_epoch,
                                  range(18)))
    assert sorted(epochs) == list(range(1, 19))
    assert len(journal.snapshot().blocked_source_ids) == 18


def test_process_writers_share_authoritative_checkpoint(tmp_path):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    script = ("import sys; from app.retrieval.policy import SQLitePolicyJournal; "
              "j=SQLitePolicyJournal(sys.argv[1]); "
              "print(j.block('credential', sys.argv[2], 'revoked').policy_epoch)")
    processes = [subprocess.Popen([sys.executable, "-c", script, str(path), f"c{i}"],
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(3)]
    results = [process.communicate(timeout=30) for process in processes]
    assert [process.returncode for process in processes] == [0, 0, 0], results
    assert sorted(int(stdout.strip()) for stdout, _ in results) == [1, 2, 3]
    assert journal.snapshot().blocked_credential_ids == ["c0", "c1", "c2"]


def test_family_registry_requirement_is_append_only_and_live(tmp_path):
    path = tmp_path / "policy.sqlite"
    writer = _journal(path, initialize=True)
    reader = _journal(path)
    first = writer.require_family_registry("f1", "a" * 64, "relation_changed")
    assert first.policy_epoch == 1
    assert first.blocked_family_ids == ["f1"]
    assert first.family_required_registry_digests == {"f1": "a" * 64}
    assert reader.snapshot() == first
    second = writer.require_family_registry("f1", "b" * 64, "another_relation")
    assert second.policy_epoch == 2
    assert second.digest != first.digest
    assert second.family_required_registry_digests == {"f1": "b" * 64}
    writer.require_family_registry("f2", "c" * 64, "relation_changed")
    assert _journal(path).snapshot().family_required_registry_digests == {
        "f1": "b" * 64, "f2": "c" * 64}
    final = writer.block("family", "f1", "administrative_hold")
    assert final.blocked_family_ids == ["f1", "f2"]
    assert final.family_required_registry_digests == {"f2": "c" * 64}
    assert final.policy_epoch == 4
    assert reader.snapshot() == final


@pytest.mark.parametrize("digest", ["", "a" * 63, "A" * 64, "x" * 64, None, 1])
def test_family_registry_requirement_requires_exact_sha256(tmp_path, digest):
    journal = _journal(tmp_path / "policy.sqlite", initialize=True)
    with pytest.raises(ValueError, match="policy_authority"):
        journal.require_family_registry("f1", digest, "relation_changed")
    assert journal.snapshot().policy_epoch == 0


def test_restore_cannot_reopen_old_family_resolution(tmp_path):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    journal.require_family_registry("f1", "a" * 64, "relation_changed")
    old_database = path.read_bytes()
    journal.require_family_registry("f1", "b" * 64, "another_relation")
    path.write_bytes(old_database)
    with pytest.raises(ValueError, match="policy_authority"):
        _journal(path)


@pytest.mark.parametrize("operation", ["block", "require_family_registry"])
def test_hold_snapshot_keeps_thread_writer_uncommitted_until_release(tmp_path, operation):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    writer = _journal(path)
    attempted = Event()
    def write():
        attempted.set()
        if operation == "block":
            return writer.block("source", "s1", "removed")
        return writer.require_family_registry("f1", "a" * 64, "relation_changed")
    with ThreadPoolExecutor(max_workers=1) as worker:
        with journal.hold_snapshot() as held:
            assert held.policy_epoch == 0
            future = worker.submit(write)
            assert attempted.wait(timeout=2)
            with pytest.raises(FutureTimeout):
                future.result(timeout=0.15)
            assert held.policy_epoch == 0
        assert future.result(timeout=5).policy_epoch == 1
    assert journal.snapshot().policy_epoch == 1


def test_hold_snapshot_keeps_process_writer_uncommitted_until_release(tmp_path):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    script = ("import sys; from app.retrieval.policy import SQLitePolicyJournal; "
              "j=SQLitePolicyJournal(sys.argv[1]); print('ready',flush=True); "
              "sys.stdin.readline(); print('attempt',flush=True); "
              "print(j.block('credential','c1','revoked').policy_epoch,flush=True)")
    process = subprocess.Popen([sys.executable, "-c", script, str(path)], stdin=subprocess.PIPE,
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        with journal.hold_snapshot() as held:
            process.stdin.write("go\n")
            process.stdin.flush()
            assert process.stdout.readline().strip() == "attempt"
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=0.15)
            assert held.blocked_credential_ids == []
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        assert stdout.strip() == "1"
        assert journal.snapshot().blocked_credential_ids == ["c1"]
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_hold_snapshot_body_exception_releases_lock_and_epoch_remains_live(tmp_path):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    with pytest.raises(RuntimeError, match="synthetic publication failure"):
        with journal.hold_snapshot() as held:
            assert held.policy_epoch == 0
            raise RuntimeError("synthetic publication failure")
    assert _journal(path).block("source", "s1", "removed").policy_epoch == 1
    with journal.hold_snapshot() as held:
        assert held.blocked_source_ids == ["s1"]


@pytest.mark.parametrize("failure", ["checkpoint_loss", "checkpoint_corrupt", "database_loss"])
def test_hold_snapshot_never_yields_missing_or_corrupted_authority(tmp_path, failure):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    checkpoint = path.with_name(path.name + ".checkpoint.json")
    if failure == "checkpoint_loss":
        checkpoint.unlink()
    elif failure == "database_loss":
        path.unlink()
    else:
        checkpoint.write_bytes(b"{}")
    with pytest.raises(ValueError, match="policy_authority"):
        with journal.hold_snapshot():
            pytest.fail("invalid authority was yielded")
    assert path.exists() is (failure != "database_loss")
    assert checkpoint.exists() is (failure != "checkpoint_loss")


@pytest.mark.parametrize("failure", ["loss", "corruption"])
def test_hold_snapshot_checks_authority_again_after_body(tmp_path, failure):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    checkpoint = path.with_name(path.name + ".checkpoint.json")
    original = checkpoint.read_bytes()
    with pytest.raises(ValueError, match="policy_authority"):
        with journal.hold_snapshot():
            if failure == "loss":
                checkpoint.unlink()
            else:
                checkpoint.write_bytes(b"{}")
    checkpoint.write_bytes(original)
    assert _journal(path).block("credential", "c1", "revoked").policy_epoch == 1


def test_hold_snapshot_refuses_reentry_without_waiting_for_own_lock(tmp_path):
    journal = _journal(tmp_path / "policy.sqlite", initialize=True)
    with journal.hold_snapshot():
        with pytest.raises(ValueError, match="policy_authority_reentrant"):
            journal.snapshot()
        with pytest.raises(ValueError, match="policy_authority_reentrant"):
            journal.block("source", "s1", "removed")
    assert journal.snapshot().policy_epoch == 0


@pytest.mark.parametrize("exception", [OSError, TypeError, KeyError, RuntimeError])
def test_hold_snapshot_preserves_body_exception_identity(tmp_path, exception):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    failure = exception("synthetic caller failure")
    with pytest.raises(exception) as captured:
        with journal.hold_snapshot():
            raise failure
    assert captured.value is failure
    assert _journal(path).block("source", "s1", "removed").policy_epoch == 1


def test_hold_snapshot_does_not_write_policy_artifacts(tmp_path):
    path = tmp_path / "policy.sqlite"
    journal = _journal(path, initialize=True)
    checkpoint = path.with_name(path.name + ".checkpoint.json")
    before = path.read_bytes(), checkpoint.read_bytes()
    with journal.hold_snapshot() as held:
        assert held.policy_epoch == 0
    assert (path.read_bytes(), checkpoint.read_bytes()) == before
