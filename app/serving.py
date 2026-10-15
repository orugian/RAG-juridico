"""Serving-time caches for a promoted generation, without weakening revocation.

The accepted P4 manager re-verifies everything on every request: it re-reads
and re-validates all artifacts, re-reads the Chroma projection and checks every
unit against the ledger with a fresh SQLite connection (and quick_check) per
lookup. That is exact but measured at ~23 min per answer on the real corpus
(3,628 units). This module keeps the same decisions and makes them cheap:

* SnapshotReviewAuthority answers ledger lookups from one in-memory snapshot of
  all current heads. The snapshot is reloaded whenever SQLite's
  ``PRAGMA data_version`` reports a commit by any other connection, so a
  revocation is visible to the very next lookup. File replacement is detected
  by inode on every lookup; full path guards and quick_check run on reload.
* ServingGenerationManager caches the verified artifact load per manifest and
  artifact file identity (re-verified on any change), and caches the unit
  review validation per exact ledger state digest (re-run on any change).
  Policy/journal, access and per-request finish checks are never cached.

The Chroma projection is verified on the first load only; citations always
come from the canonical bundle, never from Chroma (P4 contract).
"""
from collections import OrderedDict, defaultdict
import os
from pathlib import Path
import sqlite3
import threading
import time

from app.ingestion.evidence import validate_unit_decisions
from app.ingestion.review_store import ReviewRecord, ReviewSubject
from app.retrieval.generation_store import ARTIFACT_NAMES
from app.retrieval.generations import GenerationManager
from app.retrieval.review_authority import ReadOnlyReviewAuthority, _controlled_read
from app.retrieval.vector_store import ChromaVectorStore

_RELOAD_ATTEMPTS = 5


class SnapshotReviewAuthority(ReadOnlyReviewAuthority):
    def __init__(self, path: Path):
        super().__init__(path)
        self._snapshot_lock = threading.RLock()
        self._probe = None
        self._version = None
        self._heads = {}
        self._digests = {}

    def _data_version(self):
        if self._probe is None:
            self._probe = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.2,
                                          check_same_thread=False, isolation_level=None)
            self._probe.execute("PRAGMA query_only=ON")
        return self._probe.execute("PRAGMA data_version").fetchone()[0]

    def _same_file(self):
        info = self.path.stat()
        if (info.st_dev, info.st_ino) != self._identity:
            raise ValueError("review_authority_invalid")

    def _load_heads(self):
        heads = {}
        with self._connect() as database:  # full guards, schema and quick_check
            database.execute("BEGIN")
            rows = database.execute("""
                SELECT source_id, scope, subject_id, payload FROM reviews
                WHERE sequence IN (SELECT MAX(sequence) FROM reviews GROUP BY source_id, scope, subject_id)
            """).fetchall()
        for source_id, scope, subject_id, payload in rows:
            item = ReviewRecord.model_validate_json(payload)
            if item.ledger_key != (source_id, scope, subject_id):
                raise ValueError("review_authority_invalid")
            heads[(source_id, scope, subject_id)] = item
        return heads

    def _fresh(self):
        """Heads of the current committed state; reloaded after any commit."""
        with self._snapshot_lock:
            self._same_file()
            if self._version is not None and self._data_version() == self._version:
                return self._heads
            for _ in range(_RELOAD_ATTEMPTS):
                before = self._data_version()
                heads = self._load_heads()
                if self._data_version() == before:
                    self._version, self._heads, self._digests = before, heads, {}
                    return heads
            raise ValueError("review_authority_unavailable")

    @_controlled_read
    def latest(self, *, source_id, snapshot, file_hash, configuration_version,
               scope="source", subject_id=None, subject_digest=None):
        subject = ReviewSubject(source_id=source_id, scope=scope, subject_id=subject_id, subject_digest=subject_digest)
        item = self._fresh().get(subject.ledger_key)
        if item is None:
            return None
        current = (item.snapshot, item.file_hash, item.configuration_version, item.subject_digest)
        return item if item.ledger_key == subject.ledger_key and current == (
            snapshot, file_hash, configuration_version, subject.subject_digest) else None

    def _cached_digest(self, name, compute):
        with self._snapshot_lock:
            self._fresh()
            if name in self._digests:
                return self._digests[name]
            version = self._version
            value = compute()
            if self._data_version() == version:
                self._digests[name] = value
            return value

    def state_digest(self):
        return self._cached_digest("state", super().state_digest)

    def relation_state_digest(self):
        return self._cached_digest("relation", super().relation_state_digest)


class ServingVectorStore(ChromaVectorStore):
    """Chroma projection verified once, then reused for every query.

    The accepted ``query`` re-reads and re-hashes the whole collection on every
    request (measured 5-10 s with 3,769 x 1,024 vectors). Here ``read`` returns
    the last fully verified projection while the Chroma database file keeps its
    identity (inode/size) and the verification is younger than ``max_age``.
    Chroma rewrites the file's mtime on every open, so mtime is not used.
    ``query`` still checks that every returned row matches that verified
    projection and the authorized sources; citations never come from Chroma.
    """

    def __init__(self, *args, max_age_seconds: float = 600.0, clock=time.monotonic, **kwargs):
        super().__init__(*args, **kwargs)
        self._max_age, self._clock = max_age_seconds, clock
        self._verified_lock = threading.Lock()
        self._verified = {}

    def _identity(self, directory):
        if self.mode != "embedded":
            return None
        info = os.stat(Path(directory) / "chroma" / "chroma.sqlite3")
        return info.st_dev, info.st_ino, info.st_size

    def invalidate(self):
        with self._verified_lock:
            self._verified.clear()

    def read(self, directory, descriptor):
        key = (str(Path(directory).resolve()), descriptor.model_dump_json())
        identity = self._identity(directory)
        with self._verified_lock:
            cached = self._verified.get(key)
        if cached is not None and cached[0] == identity and self._clock() - cached[1] < self._max_age:
            return cached[2]
        rows = super().read(directory, descriptor)
        with self._verified_lock:
            self._verified[key] = (identity, self._clock(), rows)
        return rows


class ServingGenerationManager(GenerationManager):
    _MAX_REVIEW_ENTRIES = 16

    def __init__(self, root: Path, *, ledger, **kwargs):
        super().__init__(root, ledger=ledger, **kwargs)
        self.ledger = SnapshotReviewAuthority(self.ledger.path)
        self._cache_lock = threading.Lock()
        self._loaded = {}
        self._reviewed = OrderedDict()

    @staticmethod
    def _signature(directory):
        names = sorted((*ARTIFACT_NAMES, "manifest.json", "READY"))
        result = []
        for name in names:
            try:
                info = os.stat(Path(directory) / name)
            except FileNotFoundError:
                return None  # incomplete (e.g. staging during build): never cached
            result.append((name, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
        return tuple(result)

    def _load(self, directory, manifest, manifest_hash):
        key = (str(Path(directory).resolve()), manifest_hash)
        signature = self._signature(directory)
        if signature is None:
            return super()._load(directory, manifest, manifest_hash)
        with self._cache_lock:
            cached = self._loaded.get(key)
        if cached is None or cached[0] != signature:
            if hasattr(self.vector_store, "invalidate"):
                self.vector_store.invalidate()  # a full load always re-reads Chroma
            loaded = super()._load(directory, manifest, manifest_hash)  # full verification
            if self._signature(directory) == signature:
                with self._cache_lock:
                    self._loaded = {key: (signature, loaded)}
            return loaded
        # Same cheap authority/configuration checks as the full load, every time.
        self._configuration()
        snapshot = self._policy()
        if (snapshot.policy_epoch < manifest.policy_epoch
                or (snapshot.policy_epoch == manifest.policy_epoch and snapshot.digest != manifest.policy_digest)):
            raise ValueError("generation_policy_authority_behind_or_divergent")
        if manifest.configuration != self.configuration or manifest.journal_id != self.journal_id:
            raise ValueError("generation_configuration_or_authority_incompatible")
        return cached[1]

    def _reviews(self, bundle, *, source_ids=None):
        digest = self.ledger.state_digest()
        key = (id(bundle), None if source_ids is None else frozenset(source_ids), digest)
        with self._cache_lock:
            if self._reviewed.get(key) is bundle:
                return
        units_by_instrument = defaultdict(dict)
        for unit in bundle.units:
            units_by_instrument[unit.instrument_id][unit.unit_id] = unit
        records = {record.record_id: record for record in bundle.reviews}
        for unit in bundle.units:
            if source_ids is not None and not set(unit.source_ids).issubset(source_ids):
                continue
            # References bind only within the same instrument scope; other units
            # cannot change the decision, so pass the instrument's own units.
            for current in validate_unit_decisions(unit, self.ledger,
                                                   all_units=units_by_instrument[unit.instrument_id]):
                if records.get(current.record_id) != current:
                    raise ValueError("generation_current_review_missing_or_changed")
        if self.ledger.state_digest() == digest:
            with self._cache_lock:
                self._reviewed[key] = bundle
                while len(self._reviewed) > self._MAX_REVIEW_ENTRIES:
                    self._reviewed.popitem(last=False)
