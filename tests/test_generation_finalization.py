"""Public build/recover finalization: physical synthetic proof, never corpus approval.

Native Windows handles reproduce a sufficient Win5 mechanism; they do not
identify the owner/cause of the historical P8 incident. Fixtures are retained
under the diagnostic root. Event gates replace timing guesses in handle tests.
"""
import ctypes
import hashlib
import logging
import os
from pathlib import Path
from threading import Event, Thread
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.retrieval import generations
from tests.test_p4_generations import setup_generation as _synthetic_fixture, _access


@pytest.fixture
def physical_generation():
    diagnostic_root = (Path.cwd() / "data/evaluation/bloco1-rename-diagnostico").resolve()
    directory = diagnostic_root / ("test-" + uuid4().hex)
    assert directory.is_relative_to(diagnostic_root) and not directory.exists()
    directory.mkdir(parents=True)
    return _synthetic_fixture.__wrapped__(directory.resolve())


class DirectoryHandle:
    """A real GENERIC_READ descendant handle; release is acknowledged by Event."""
    def __init__(self, path):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        self.opened, self.release, self.closed = Event(), Event(), Event()
        self.errors = []
        def hold():
            handle = kernel.CreateFileW(str(path), 0x80000000, 1 | 2, None, 3, 0x02000000, None)
            if handle == ctypes.c_void_p(-1).value:
                self.errors.append(ctypes.WinError(ctypes.get_last_error()))
                self.opened.set()
                self.closed.set()
                return
            self.opened.set()
            self.release.wait()
            if not kernel.CloseHandle(handle):
                self.errors.append(ctypes.WinError(ctypes.get_last_error()))
            self.closed.set()
        self.thread = Thread(target=hold, daemon=True)
        self.thread.start()
        try:
            assert self.opened.wait(5), "native handle creation timed out"
            assert not self.errors
        except BaseException:
            self.release.set()
            self.thread.join(5)
            raise

    def close(self):
        self.release.set()
        assert self.closed.wait(5), "native handle release timed out"
        self.thread.join(5)
        assert not self.thread.is_alive() and not self.errors


def canonical_hashes(directory):
    names = ("READY", "manifest.json", "bundle.json", "chunks.jsonl", "vectors.json", "lexical.json", "bm25.joblib")
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}


@pytest.mark.skipif(os.name != "nt", reason="real Windows directory HANDLE required")
def test_build_finishes_after_real_descendant_handle_is_released(physical_generation, monkeypatch, caplog):
    manager, bundle, paths, *_ = physical_generation
    stage, destination = manager.root / ".staging" / "candidate", manager.root / "candidate"
    holders, waits, before = [], [], []
    caplog.set_level(logging.INFO, logger=generations.__name__)
    def release_on_wait(delay):
        assert stage.is_dir() and not destination.exists()
        waits.append(delay)
        holders[0].close()
    def fault(point):
        if point == "after_ready":
            before.append(canonical_hashes(stage))
            holders.append(DirectoryHandle(stage / "chroma"))
            monkeypatch.setattr(generations, "time", SimpleNamespace(monotonic=time.monotonic, sleep=release_on_wait))
    try:
        manifest = manager.build("candidate", bundle, source_paths=paths, fault=fault)
    finally:
        for holder in holders:
            holder.close()
    assert manifest.generation_id == "candidate" and waits and all(0 < delay <= 0.05 for delay in waits)
    assert not stage.exists() and destination.is_dir()
    assert canonical_hashes(destination) == before[0]
    assert (manager.root / ".reservations" / "candidate").is_dir()
    assert not (manager.root / "active.json").exists()
    assert manager.verify("candidate").artifacts.bundle == bundle
    assert "generation_finalization_recovered" in caplog.text
    assert str(stage) not in caplog.text


def ready_stage(manager, bundle, paths, generation_id="candidate"):
    def stop(point):
        if point == "after_ready":
            raise RuntimeError("synthetic READY stop")
    with pytest.raises(RuntimeError, match="synthetic READY stop"):
        manager.build(generation_id, bundle, source_paths=paths, fault=stop)
    return manager.root / ".staging" / generation_id


@pytest.mark.skipif(os.name != "nt", reason="real Windows directory HANDLE required")
def test_recover_finishes_after_real_descendant_handle_is_released(physical_generation, monkeypatch, caplog):
    manager, bundle, paths, _, journal, _ = physical_generation
    stage = ready_stage(manager, bundle, paths)
    before = canonical_hashes(stage)
    holder, waits = DirectoryHandle(stage / "chroma"), []
    caplog.set_level(logging.INFO, logger=generations.__name__)
    def release_on_wait(delay):
        waits.append(delay)
        holder.close()
    monkeypatch.setattr(generations, "time", SimpleNamespace(monotonic=time.monotonic, sleep=release_on_wait))
    try:
        manifest = manager.recover("candidate", access=_access(journal))
    finally:
        holder.close()
    assert manifest.generation_id == "candidate" and waits
    assert not stage.exists() and canonical_hashes(manager.root / "candidate") == before
    assert manager.verify("candidate").artifacts.bundle == bundle
    assert not (manager.root / "active.json").exists()
    assert (manager.root / ".reservations" / "candidate").is_dir()
    assert "generation_finalization_recovered" in caplog.text


class LogicalClock:
    def __init__(self, on_wait=None):
        self.now, self.delays, self.on_wait = 0.0, [], on_wait
    def monotonic(self):
        return self.now
    def sleep(self, delay):
        assert 0 < delay <= 0.05
        self.delays.append(delay)
        self.now += delay
        if self.on_wait is not None:
            self.on_wait()


@pytest.mark.skipif(os.name != "nt", reason="real Windows directory HANDLE required")
@pytest.mark.parametrize("operation", ["build", "recover"])
def test_persistent_handle_exhaustion_preserves_ready_and_active_then_explicit_recovery(
        physical_generation, monkeypatch, caplog, operation):
    manager, bundle, paths, _, journal, ledger = physical_generation
    manager.build("previous", bundle, source_paths=paths)
    manager.promote("previous", access=_access(journal))
    pointer = (manager.root / "active.json").read_bytes()
    stage, destination = manager.root / ".staging" / "candidate", manager.root / "candidate"
    holders, before = [], []
    clock = LogicalClock()
    caplog.set_level(logging.INFO, logger=generations.__name__)
    def lock(point):
        if point == ("after_ready" if operation == "build" else "before_recovery"):
            before.append(canonical_hashes(stage))
            holders.append(DirectoryHandle(stage / "chroma"))
            monkeypatch.setattr(generations, "time", clock)
    if operation == "recover":
        ready_stage(manager, bundle, paths)
    try:
        with pytest.raises(ValueError, match="^generation_finalization_unavailable$") as failure:
            if operation == "build":
                manager.build("candidate", bundle, source_paths=paths, fault=lock)
            else:
                manager.recover("candidate", access=_access(journal), fault=lock)
        assert failure.value.__cause__.winerror == 5
        assert sum(clock.delays) == pytest.approx(2.0)
        assert stage.is_dir() and not destination.exists()
        assert canonical_hashes(stage) == before[0]
        assert (manager.root / ".reservations" / "candidate").is_dir()
        assert (manager.root / "active.json").read_bytes() == pointer
        assert "generation_finalization_unavailable" in caplog.text
        assert "generation_finalization_recovered" not in caplog.text
        assert str(stage) not in caplog.text and "Acesso negado" not in caplog.text
        with pytest.raises(ValueError, match="generation_id_immutable"):
            manager.build("candidate", bundle, source_paths=paths)
        # Exhaustion unwinds both authorities and writer lease; normal reads still work.
        assert manager.readiness() and journal.snapshot().policy_epoch == 0
        assert ledger.state_digest() == manager.ledger.state_digest()
    finally:
        for holder in holders:
            holder.close()
    manager.recover("candidate", access=_access(journal))
    assert not stage.exists() and canonical_hashes(destination) == before[0]
    assert (manager.root / "active.json").read_bytes() == pointer
    assert manager.verify("candidate").artifacts.bundle == bundle


@pytest.mark.parametrize("operation", ["build", "recover"])
@pytest.mark.parametrize("platform,winerror", [("linux", 5), ("win32", None), ("win32", 2), ("win32", 183)])
def test_non_windows_or_non_allowlisted_errors_are_not_retried(
        physical_generation, monkeypatch, caplog, operation, platform, winerror):
    manager, bundle, paths, _, journal, _ = physical_generation
    stage = manager.root / ".staging" / "candidate"
    destination = manager.root / "candidate"
    if operation == "recover":
        ready_stage(manager, bundle, paths)
    attempts, waits = [], []
    original_rename = os.rename
    error = OSError(13, "synthetic nonretryable")
    if winerror is not None:
        error.winerror = winerror
    def fail(origin, target):
        if Path(origin) == stage and Path(target) == destination:
            attempts.append(1)
            raise error
        return original_rename(origin, target)
    def refuse_wait(delay):
        waits.append(delay)
        raise AssertionError("nonretryable rename must not wait")
    def inject(point):
        if point == ("after_ready" if operation == "build" else "before_recovery"):
            monkeypatch.setattr(generations, "sys", SimpleNamespace(platform=platform))
            monkeypatch.setattr(generations.os, "rename", fail)
            monkeypatch.setattr(generations, "time", SimpleNamespace(monotonic=time.monotonic, sleep=refuse_wait))
    with pytest.raises(ValueError, match="review_authority_unavailable") as failure:
        if operation == "build":
            manager.build("candidate", bundle, source_paths=paths, fault=inject)
        else:
            manager.recover("candidate", access=_access(journal), fault=inject)
    assert failure.value.__cause__ is error
    assert attempts == [1] and not waits
    assert stage.is_dir() and not destination.exists()
    assert "generation_finalization_recovered" not in caplog.text


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_allowlisted_native_error_recovers_but_retry_is_observable(physical_generation, monkeypatch, caplog, winerror):
    manager, bundle, paths, *_ = physical_generation
    stage, destination = manager.root / ".staging" / "candidate", manager.root / "candidate"
    original_rename, attempts = os.rename, []
    clock = LogicalClock()
    caplog.set_level(logging.INFO, logger=generations.__name__)
    def once(origin, target):
        if Path(origin) == stage and Path(target) == destination:
            attempts.append(1)
            if len(attempts) == 1:
                error = OSError(13, "synthetic native contention")
                error.winerror = winerror
                raise error
        return original_rename(origin, target)
    def inject(point):
        if point == "after_ready":
            monkeypatch.setattr(generations, "sys", SimpleNamespace(platform="win32"))
            monkeypatch.setattr(generations.os, "rename", once)
            monkeypatch.setattr(generations, "time", clock)
    manager.build("candidate", bundle, source_paths=paths, fault=inject)
    assert attempts == [1, 1] and clock.delays == [0.05]
    assert manager.verify("candidate").artifacts.bundle == bundle
    assert f"generation_finalization_recovered attempts=2 winerror={winerror}" in caplog.text


@pytest.mark.parametrize("operation", ["build", "recover"])
def test_destination_created_between_attempts_is_never_overwritten(physical_generation, monkeypatch, operation):
    manager, bundle, paths, _, journal, _ = physical_generation
    stage, destination = manager.root / ".staging" / "candidate", manager.root / "candidate"
    if operation == "recover":
        ready_stage(manager, bundle, paths)
    original_rename, attempts = os.rename, []
    def fail_first(origin, target):
        if Path(origin) == stage and Path(target) == destination:
            attempts.append(1)
            error = OSError(13, "synthetic contention")
            error.winerror = 5
            raise error
        return original_rename(origin, target)
    clock = LogicalClock(on_wait=lambda: destination.mkdir())
    def inject(point):
        if point == ("after_ready" if operation == "build" else "before_recovery"):
            monkeypatch.setattr(generations, "sys", SimpleNamespace(platform="win32"))
            monkeypatch.setattr(generations.os, "rename", fail_first)
            monkeypatch.setattr(generations, "time", clock)
    with pytest.raises(ValueError, match="generation_id_immutable"):
        if operation == "build":
            manager.build("candidate", bundle, source_paths=paths, fault=inject)
        else:
            manager.recover("candidate", access=_access(journal), fault=inject)
    assert attempts == [1] and len(clock.delays) == 1
    assert stage.is_dir() and (stage / "READY").is_file()
    assert destination.is_dir() and list(destination.iterdir()) == []


@pytest.mark.parametrize("operation", ["build", "recover"])
def test_zero_budget_still_makes_one_atomic_attempt(physical_generation, monkeypatch, operation):
    manager, bundle, paths, _, journal, _ = physical_generation
    stage, destination = manager.root / ".staging" / "candidate", manager.root / "candidate"
    if operation == "recover":
        ready_stage(manager, bundle, paths)
    original_rename, attempts = os.rename, []
    def record(origin, target):
        if Path(origin) == stage and Path(target) == destination:
            attempts.append(1)
        return original_rename(origin, target)
    def inject(point):
        if point == ("after_ready" if operation == "build" else "before_recovery"):
            monkeypatch.setattr(generations, "_FINALIZATION_TIMEOUT_SECONDS", 0)
            monkeypatch.setattr(generations.os, "rename", record)
    if operation == "build":
        manager.build("candidate", bundle, source_paths=paths, fault=inject)
    else:
        manager.recover("candidate", access=_access(journal), fault=inject)
    assert attempts == [1]
    assert manager.verify("candidate").artifacts.bundle == bundle


@pytest.mark.skipif(os.name != "nt", reason="real Windows directory HANDLE required")
@pytest.mark.parametrize("operation", ["build", "recover"])
@pytest.mark.parametrize("scope", ["unit", "policy"])
def test_retry_keeps_current_authorities_until_atomic_finalization(
        physical_generation, monkeypatch, operation, scope):
    from app.ingestion.review_store import unit_review_digest
    manager, bundle, paths, _, journal, ledger = physical_generation
    stage = manager.root / ".staging" / "candidate"
    if operation == "recover":
        ready_stage(manager, bundle, paths)
    started, acknowledged = Event(), Event()
    holders, threads, errors = [], [], []
    source, unit = bundle.sources[1], bundle.units[1]
    def writer():
        started.set()
        try:
            if scope == "unit":
                ledger.record(source_id=source.source_id, snapshot=source.snapshot, file_hash=source.file_hash,
                    configuration_version=source.configuration_version, decision="quarantined", reviewer="synthetic",
                    reason="synthetic finalization race", scope="unit", subject_id=unit.unit_id,
                    subject_digest=unit_review_digest(unit))
            else:
                journal.block("source", source.source_id, "synthetic_finalization_race")
            acknowledged.set()
        except Exception as error:
            errors.append(error)
    def on_wait():
        thread = Thread(target=writer)
        threads.append(thread)
        thread.start()
        assert started.wait(5)
        # Condition timeout checks absence of ACK; not a sleep for the rename.
        assert not acknowledged.wait(0.1), "authority ACK preceded finalization while guards held"
        holders[0].close()
    clock = LogicalClock(on_wait=on_wait)
    def lock(point):
        if point == ("after_ready" if operation == "build" else "before_recovery"):
            holders.append(DirectoryHandle(stage / "chroma"))
            monkeypatch.setattr(generations, "time", clock)
    try:
        if operation == "build":
            manifest = manager.build("candidate", bundle, source_paths=paths, fault=lock)
        else:
            manifest = manager.recover("candidate", access=_access(journal), fault=lock)
    finally:
        for holder in holders:
            holder.close()
        for thread in threads:
            thread.join(10)
    assert not errors and acknowledged.is_set() and all(not thread.is_alive() for thread in threads)
    assert len(clock.delays) == 1 and manifest.generation_id == "candidate"
    assert not stage.exists() and (manager.root / "candidate" / "READY").is_file()
    assert not (manager.root / "active.json").exists()
    if scope == "unit":
        assert ledger.latest(source_id=source.source_id, snapshot=source.snapshot,
            file_hash=source.file_hash, configuration_version=source.configuration_version,
            scope="unit", subject_id=unit.unit_id, subject_digest=unit_review_digest(unit)).decision == "quarantined"
    else:
        assert source.source_id in journal.snapshot().blocked_source_ids


def test_recovery_rechecks_revocation_before_entering_finalization(physical_generation, monkeypatch):
    manager, bundle, paths, _, journal, _ = physical_generation
    stage = ready_stage(manager, bundle, paths)
    before, attempts = canonical_hashes(stage), []
    access = _access(journal)
    original_rename = os.rename
    def record(origin, target):
        attempts.append(1)
        return original_rename(origin, target)
    monkeypatch.setattr(generations.os, "rename", record)
    def revoke(point):
        if point == "before_recovery":
            journal.block("credential", access.credential_id, "synthetic_finalization_revoke")
    with pytest.raises(ValueError, match="generation_access_denied_or_stale"):
        manager.recover("candidate", access=access, fault=revoke)
    assert not attempts and canonical_hashes(stage) == before
    assert not (manager.root / "candidate").exists() and not (manager.root / "active.json").exists()
    assert access.credential_id in journal.snapshot().blocked_credential_ids


@pytest.mark.parametrize("operation", ["build", "recover"])
def test_elapsed_budget_during_path_guard_precheck_stops_before_additional_rename(
        physical_generation, monkeypatch, caplog, operation):
    """A path guard may consume time after the post-sleep check (R1-B1-J1).

    Once the observed deadline elapsed, no additional rename attempt may start,
    even if it is reached during re-entry path validation. The first attempt is
    always allowed; this bound governs retries only, not hard-real-time syscalls.
    """
    manager, bundle, paths, _, journal, _ = physical_generation
    stage, destination = manager.root / ".staging" / "candidate", manager.root / "candidate"
    if operation == "recover":
        ready_stage(manager, bundle, paths)
    original_rename, original_safe = os.rename, generations._safe_path
    calls, safe_visits, now = [], [], [0.0]
    error = OSError(13, "synthetic native contention")
    error.winerror = 5
    def fail_first(origin, target):
        calls.append(now[0])
        if len(calls) == 1:
            raise error
        return original_rename(origin, target)
    def guarded(path):
        safe_visits.append(path)
        # Third guard is the first one of retry re-entry: it consumes the budget.
        if len(safe_visits) == 3:
            now[0] = 2.25
        return original_safe(path)
    def sleep(delay):
        now[0] += delay
    caplog.set_level(logging.INFO, logger=generations.__name__)
    def inject(point):
        if point == ("after_ready" if operation == "build" else "before_recovery"):
            monkeypatch.setattr(generations, "sys", SimpleNamespace(platform="win32"))
            monkeypatch.setattr(generations.os, "rename", fail_first)
            monkeypatch.setattr(generations, "_safe_path", guarded)
            monkeypatch.setattr(generations, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep))
    with pytest.raises(ValueError, match="^generation_finalization_unavailable$") as failure:
        if operation == "build":
            manager.build("candidate", bundle, source_paths=paths, fault=inject)
        else:
            manager.recover("candidate", access=_access(journal), fault=inject)
    assert failure.value.__cause__ is error
    assert calls == [0.0], "no rename may start after the observed deadline elapsed during guards"
    assert stage.is_dir() and (stage / "READY").is_file() and not destination.exists()
    assert (manager.root / ".reservations" / "candidate").is_dir()
    assert "generation_finalization_unavailable attempts=1 winerror=5" in caplog.text
    assert "generation_finalization_recovered" not in caplog.text
    assert str(stage) not in caplog.text


@pytest.mark.skipif(os.name != "nt", reason="real Windows writer lease required")
@pytest.mark.parametrize("operation", ["build", "recover"])
def test_retry_keeps_writer_lease_and_releases_it_on_success(physical_generation, monkeypatch, operation):
    import msvcrt
    manager, bundle, paths, _, journal, _ = physical_generation
    stage = manager.root / ".staging" / "candidate"
    if operation == "recover":
        ready_stage(manager, bundle, paths)
    holders, denied, threads = [], [], []
    attempted = Event()
    def contender():
        try:
            with (manager.root / "writer.lock").open("r+b") as target:
                try:
                    msvcrt.locking(target.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    denied.append(error)
                else:
                    msvcrt.locking(target.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            attempted.set()
    def on_wait():
        thread = Thread(target=contender)
        threads.append(thread)
        thread.start()
        assert attempted.wait(5) and len(denied) == 1
        holders[0].close()
    clock = LogicalClock(on_wait=on_wait)
    def lock(point):
        if point == ("after_ready" if operation == "build" else "before_recovery"):
            holders.append(DirectoryHandle(stage / "chroma"))
            monkeypatch.setattr(generations, "time", clock)
    try:
        if operation == "build":
            manager.build("candidate", bundle, source_paths=paths, fault=lock)
        else:
            manager.recover("candidate", access=_access(journal), fault=lock)
    finally:
        for holder in holders:
            holder.close()
        for thread in threads:
            thread.join(5)
    with (manager.root / "writer.lock").open("r+b") as target:
        msvcrt.locking(target.fileno(), msvcrt.LK_NBLCK, 1)
        msvcrt.locking(target.fileno(), msvcrt.LK_UNLCK, 1)
    assert len(clock.delays) == 1 and not stage.exists()


@pytest.mark.parametrize("operation", ["build", "recover"])
def test_destination_existing_before_first_attempt_is_not_overwritten(physical_generation, monkeypatch, operation):
    manager, bundle, paths, _, journal, _ = physical_generation
    stage, destination = manager.root / ".staging" / "candidate", manager.root / "candidate"
    if operation == "recover":
        ready_stage(manager, bundle, paths)
    attempts, original_rename = [], os.rename
    def record(origin, target):
        attempts.append(1)
        return original_rename(origin, target)
    def occupied(point):
        if point == ("after_ready" if operation == "build" else "before_recovery"):
            # Empty directories can otherwise be replaced by POSIX os.rename.
            destination.mkdir()
            monkeypatch.setattr(generations, "sys", SimpleNamespace(platform="linux"))
            monkeypatch.setattr(generations.os, "rename", record)
    with pytest.raises(ValueError, match="generation_id_immutable"):
        if operation == "build":
            manager.build("candidate", bundle, source_paths=paths, fault=occupied)
        else:
            manager.recover("candidate", access=_access(journal), fault=occupied)
    assert not attempts and stage.is_dir() and canonical_hashes(stage)
    assert destination.is_dir() and list(destination.iterdir()) == []


@pytest.mark.parametrize("operation", ["build", "recover"])
def test_elapsed_wait_budget_never_starts_an_additional_attempt(physical_generation, monkeypatch, operation):
    manager, bundle, paths, _, journal, _ = physical_generation
    stage, destination = manager.root / ".staging" / "candidate", manager.root / "candidate"
    if operation == "recover":
        ready_stage(manager, bundle, paths)
    attempts, original_rename, now = [], os.rename, [0.0]
    error = OSError(13, "synthetic scheduler overrun")
    error.winerror = 5
    def fail(origin, target):
        if Path(origin) == stage and Path(target) == destination:
            attempts.append(1)
            raise error
        return original_rename(origin, target)
    def late(delay):
        assert 0 < delay <= 0.05
        now[0] = 3.0
    def inject(point):
        if point == ("after_ready" if operation == "build" else "before_recovery"):
            monkeypatch.setattr(generations, "sys", SimpleNamespace(platform="win32"))
            monkeypatch.setattr(generations.os, "rename", fail)
            monkeypatch.setattr(generations, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=late))
    with pytest.raises(ValueError, match="^generation_finalization_unavailable$") as failure:
        if operation == "build":
            manager.build("candidate", bundle, source_paths=paths, fault=inject)
        else:
            manager.recover("candidate", access=_access(journal), fault=inject)
    assert failure.value.__cause__ is error and attempts == [1]
    assert stage.is_dir() and (stage / "READY").is_file() and not destination.exists()
