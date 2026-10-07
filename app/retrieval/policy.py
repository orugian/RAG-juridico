"""Explicit local policy authority for synthetic P4 exercises.

Keep this directory outside generation/backup roots. SQLite FULL commits precede
an independently checked, fsynced checkpoint and acknowledgement. Interrupted
checkpoint writes fail closed, not automatic repair. This is NOT AWS durability:
rollback/loss of the entire authority requires an independently trusted identity
and minimum epoch; D04 must choose the real external source of current policy.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import threading
import time
from typing import ContextManager, Literal, Protocol
from uuid import uuid4

from app.retrieval.generation_contracts import PolicySnapshot, canonical_bytes, content_digest

PolicyScope = Literal["source", "credential", "family"]
_HELD_LOCKS = threading.local()


class PolicyJournal(Protocol):
    def snapshot(self) -> PolicySnapshot: ...

    def hold_snapshot(self) -> ContextManager[PolicySnapshot]: ...

    def block(self, scope: PolicyScope, subject_id: str, reason_code: str) -> PolicySnapshot: ...

    def require_family_registry(self, family_id: str, registry_digest: str, reason_code: str) -> PolicySnapshot: ...


_TRIGGERS = {
    f"policy_{table}_no_{operation}": (
        f"CREATE TRIGGER policy_{table}_no_{operation} BEFORE {operation.upper()} "
        f"ON policy_{table} BEGIN SELECT RAISE(ABORT, 'append-only policy authority'); END"
    )
    for table in ("events", "identity") for operation in ("update", "delete")
}


def _identifier(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 512 or any(ord(character) < 32 for character in value)):
        raise ValueError("policy_authority_identifier_invalid")


def _block_values(scope, subject_id, reason_code):
    if scope not in ("source", "credential", "family"):
        raise ValueError("policy_authority_scope_invalid")
    _identifier(subject_id)
    if not isinstance(reason_code, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", reason_code):
        raise ValueError("policy_authority_reason_code_invalid")


class SQLitePolicyJournal:
    """No implicit create, migration, reset, unblock, or stale-policy cache."""

    def __init__(self, path: Path, *, initialize: bool = False, journal_id: str | None = None):
        supplied = Path(path).absolute()
        if supplied.is_symlink():
            raise ValueError("policy_authority_symlink")
        self.path = supplied.parent.resolve() / supplied.name
        self.checkpoint_path = self.path.with_name(self.path.name + ".checkpoint.json")
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.journal_id = journal_id
        self._file_identity = None
        self._lock_identity = None
        self._observed_epoch = -1
        self._observed_digest = None
        if journal_id is not None:
            _identifier(journal_id)
            if len(journal_id) > 128:
                raise ValueError("policy_authority_identity_invalid")
        if initialize:
            self._initialize(journal_id or str(uuid4()))
        self._check_files()
        self._file_identity = self._identity(self.path)
        self._lock_identity = self._identity(self.lock_path)
        initial = self.snapshot()
        self.journal_id = initial.journal_id

    @staticmethod
    def _identity(path):
        value = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(value.st_mode):
            raise ValueError("policy_authority_artifact_invalid")
        return value.st_dev, value.st_ino

    def _check_files(self):
        try:
            for path in (self.path, self.checkpoint_path, self.lock_path):
                self._identity(path)
            if (self._file_identity is not None and self._identity(self.path) != self._file_identity
                    or self._lock_identity is not None and self._identity(self.lock_path) != self._lock_identity):
                raise ValueError("policy_authority_identity_changed")
        except OSError as error:
            raise ValueError("policy_authority_unavailable") from error

    def _initialize(self, journal_id):
        if any(path.exists() or path.is_symlink() for path in (self.path, self.checkpoint_path, self.lock_path)):
            raise ValueError("policy_authority_already_exists")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.lock_path.open("xb") as lock_file:
                lock_file.write(b"1")
                lock_file.flush()
                os.fsync(lock_file.fileno())
            with self.path.open("xb"):
                pass
            # Bootstrap is explicit and exclusive. A failure leaves an incomplete
            # authority, requiring operator investigation, never a silent retry.
            with self._locked(require_checkpoint=False):
                with self._database() as database:
                    database.execute("BEGIN IMMEDIATE")
                    database.execute("CREATE TABLE policy_identity(journal_id TEXT NOT NULL, schema_version TEXT NOT NULL)")
                    database.execute("CREATE TABLE policy_events(epoch INTEGER PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL)")
                    database.execute("CREATE TABLE policy_head(singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch INTEGER NOT NULL, digest TEXT NOT NULL)")
                    database.execute("INSERT INTO policy_identity VALUES(?, 'policy-v1')", (journal_id,))
                    genesis = content_digest({"journal_id": journal_id, "schema_version": "policy-v1"})
                    database.execute("INSERT INTO policy_head VALUES(1, 0, ?)", (genesis,))
                    for statement in _TRIGGERS.values():
                        database.execute(statement)
                    database.commit()
                self._write_checkpoint(journal_id, 0, genesis)
        except (OSError, sqlite3.Error) as error:
            raise ValueError("policy_authority_bootstrap_failed") from error

    @contextmanager
    def _locked(self, *, require_checkpoint=True):
        held = getattr(_HELD_LOCKS, "paths", None)
        if held is None:
            held = _HELD_LOCKS.paths = set()
        lock_key = os.path.normcase(str(self.lock_path))
        if lock_key in held:
            raise ValueError("policy_authority_reentrant_lock")
        if require_checkpoint:
            self._check_files()
        body_failure = None
        try:
            with self.lock_path.open("r+b") as lock_file:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        if os.name == "nt":
                            import msvcrt
                            lock_file.seek(0)
                            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except (OSError, BlockingIOError):
                        if time.monotonic() >= deadline:
                            raise ValueError("policy_authority_lock_unavailable")
                        time.sleep(0.01)
                held.add(lock_key)
                try:
                    if require_checkpoint:
                        self._check_files()
                    try:
                        yield
                    except BaseException as error:
                        body_failure = error
                        raise
                finally:
                    try:
                        if os.name == "nt":
                            lock_file.seek(0)
                            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    finally:
                        held.remove(lock_key)
        except OSError as error:
            if error is body_failure:
                raise
            raise ValueError("policy_authority_unavailable") from error

    @contextmanager
    def _database(self):
        # mode=rw refuses creation if a race or loss removes the authority.
        database = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        try:
            database.execute("PRAGMA synchronous=FULL")
            if database.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise ValueError("policy_authority_journal_mode_changed")
            yield database
        finally:
            database.close()

    def _write_checkpoint(self, journal_id, epoch, digest):
        checkpoint = {"schema_version": "policy-v1", "journal_id": journal_id,
                      "policy_epoch": epoch, "digest": digest}
        descriptor, temporary = tempfile.mkstemp(prefix=self.path.name + ".checkpoint-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(canonical_bytes(checkpoint))
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.checkpoint_path)
            # Windows fsync on the file is verified; directory fsync is available
            # on POSIX. Neither is a claim about physical disk failure durability.
            if os.name != "nt":
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read(self, database):
        if database.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("policy_authority_corrupted")
        identity = database.execute("SELECT journal_id, schema_version FROM policy_identity").fetchall()
        if len(identity) != 1 or identity[0][1] != "policy-v1":
            raise ValueError("policy_authority_identity_invalid")
        journal_id = identity[0][0]
        if self.journal_id is not None and self.journal_id != journal_id:
            raise ValueError("policy_authority_identity_changed")
        triggers = dict(database.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        for name, expected in _TRIGGERS.items():
            if triggers.get(name) != expected:
                raise ValueError("policy_authority_append_only_protection_changed")
        epoch = 0
        digest = content_digest({"journal_id": journal_id, "schema_version": "policy-v1"})
        blocked = {"source": set(), "credential": set(), "family": set()}
        family_requirements = {}
        for row_epoch, payload, row_digest in database.execute("SELECT epoch,payload,digest FROM policy_events ORDER BY epoch"):
            event = json.loads(payload)
            if (not isinstance(event, dict) or set(event) != {"journal_id", "epoch", "scope", "subject_id", "reason_code", "previous_digest", "registry_digest"}
                    or type(event["epoch"]) is not int or event["epoch"] != epoch + 1
                    or row_epoch != event["epoch"] or event["journal_id"] != journal_id
                    or event["previous_digest"] != digest or content_digest(event) != row_digest
                    or canonical_bytes(event).decode("utf-8") != payload):
                raise ValueError("policy_authority_chain_invalid")
            _block_values(event["scope"], event["subject_id"], event["reason_code"])
            registry_digest = event["registry_digest"]
            if registry_digest is not None and (event["scope"] != "family" or not isinstance(registry_digest, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", registry_digest)):
                raise ValueError("policy_authority_family_requirement_invalid")
            blocked[event["scope"]].add(event["subject_id"])
            if event["scope"] == "family":
                if registry_digest is None:
                    family_requirements.pop(event["subject_id"], None)
                else:
                    family_requirements[event["subject_id"]] = registry_digest
            epoch, digest = row_epoch, row_digest
        if database.execute("SELECT singleton,epoch,digest FROM policy_head").fetchall() != [(1, epoch, digest)]:
            raise ValueError("policy_authority_head_invalid")
        checkpoint = json.loads(self.checkpoint_path.read_bytes())
        expected = {"schema_version": "policy-v1", "journal_id": journal_id,
                    "policy_epoch": epoch, "digest": digest}
        if checkpoint != expected or canonical_bytes(expected) != self.checkpoint_path.read_bytes():
            raise ValueError("policy_authority_checkpoint_invalid")
        # This is only a live-process rollback witness, never a stale-policy
        # cache. A cold start still needs externally pinned identity/minimum epoch.
        if (epoch < self._observed_epoch or epoch == self._observed_epoch and digest != self._observed_digest):
            raise ValueError("policy_authority_rollback_detected")
        self._observed_epoch, self._observed_digest = epoch, digest
        return PolicySnapshot(journal_id=journal_id, policy_epoch=epoch, digest=digest,
                              blocked_source_ids=sorted(blocked["source"]),
                              blocked_credential_ids=sorted(blocked["credential"]),
                              blocked_family_ids=sorted(blocked["family"]),
                              family_required_registry_digests=dict(sorted(family_requirements.items())))

    def snapshot(self) -> PolicySnapshot:
        try:
            with self._locked(), self._database() as database:
                database.execute("BEGIN")
                result = self._read(database)
                self._check_files()
                return result
        except (OSError, sqlite3.Error, json.JSONDecodeError, TypeError, KeyError) as error:
            raise ValueError("policy_authority_invalid_or_unavailable") from error

    @contextmanager
    def hold_snapshot(self):
        """Hold live policy against cooperative commits through publication.

        Consume the yielded snapshot: calling snapshot/block/another hold on the
        same thread and authority is rejected, not a reentrant read or fallback.
        This short local critical section does not promise external AWS policy
        atomicity. The generation caller also holds its current review authority.
        """
        body_failure = None
        try:
            with self._locked(), self._database() as database:
                database.execute("PRAGMA query_only=ON")
                database.execute("BEGIN")
                initial = self._read(database)
                self._check_files()
                expected = initial.model_copy(deep=True)
                try:
                    yield initial
                except BaseException as error:
                    body_failure = error
                    raise
                finally:
                    # Even exceptional body exits check loss/mismatch, then all
                    # resources unwind. Never silently repair or acknowledge.
                    self._check_files()
                    if self._read(database) != expected:
                        raise ValueError("policy_authority_changed_during_hold")
        except (OSError, sqlite3.Error, json.JSONDecodeError, TypeError, KeyError) as error:
            if error is body_failure:
                raise
            raise ValueError("policy_authority_invalid_or_unavailable") from error

    def block(self, scope: PolicyScope, subject_id: str, reason_code: str) -> PolicySnapshot:
        """Administrative block: families have no automatic reconciliation path."""
        return self._append(scope, subject_id, reason_code, registry_digest=None)

    def require_family_registry(self, family_id: str, registry_digest: str, reason_code: str) -> PolicySnapshot:
        """Record a new family requirement, never grant access or adjudicate proof.

        Generation admission must additionally verify its complete approved
        resolution against this digest AND the current relation review registry.
        Historical support and source/credential blocks are separate invariants.
        """
        if not isinstance(registry_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", registry_digest):
            raise ValueError("policy_authority_family_requirement_invalid")
        return self._append("family", family_id, reason_code, registry_digest=registry_digest)

    def _append(self, scope, subject_id, reason_code, *, registry_digest):
        _block_values(scope, subject_id, reason_code)
        try:
            with self._locked(), self._database() as database:
                database.execute("BEGIN IMMEDIATE")
                before = self._read(database)
                event = {"journal_id": before.journal_id, "epoch": before.policy_epoch + 1,
                         "scope": scope, "subject_id": subject_id, "reason_code": reason_code,
                         "previous_digest": before.digest, "registry_digest": registry_digest}
                digest = content_digest(event)
                database.execute("INSERT INTO policy_events VALUES(?,?,?)", (event["epoch"], canonical_bytes(event).decode("utf-8"), digest))
                database.execute("UPDATE policy_head SET epoch=?,digest=? WHERE singleton=1", (event["epoch"], digest))
                database.commit()
                self._write_checkpoint(before.journal_id, event["epoch"], digest)
                # Validation after commit/checkpoint: no acknowledgement on either
                # persistence failure. All readers/writers hold the same OS lock.
                database.execute("BEGIN")
                result = self._read(database)
                self._check_files()
                return result
        except (OSError, sqlite3.Error, json.JSONDecodeError, TypeError, KeyError) as error:
            raise ValueError("policy_authority_commit_failed") from error
