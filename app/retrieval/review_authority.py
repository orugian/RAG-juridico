"""Consume an existing P3B ledger without bootstrap, migration or SQL writes.

SQLite mode=ro observes committed WAL updates; immutable=1 would incorrectly
ignore them. SQLite may maintain its own WAL/SHM coordination sidecars in a
writable operator-controlled directory. That is not ledger/schema creation or
approval, and the main database bytes are never written by this adapter.

Publication has an explicitly authorized non-mutating lease: hold_current uses
mode=rw (never create) for BEGIN IMMEDIATE, then query_only=ON and ROLLBACK.
That reserved transaction blocks ReviewStore writers through the pointer swap.

Live inode checks detect loss/replacement, not a disk snapshot rollback in a
new process. Operational freshness still requires the external D04 authority.
"""
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import sqlite3
import stat

from pydantic import ValidationError

from app.ingestion.review_store import ReviewStore


def _sqlite_failure(exc: sqlite3.Error) -> ValueError:
    code = getattr(exc, "sqlite_errorcode", 0) & 255
    unavailable = {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_CANTOPEN,
                   sqlite3.SQLITE_IOERR, sqlite3.SQLITE_READONLY, sqlite3.SQLITE_PERM}
    return ValueError("review_authority_unavailable" if code in unavailable else "review_authority_invalid")


def _controlled_read(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except sqlite3.Error as exc:
            raise _sqlite_failure(exc) from exc
        except OSError as exc:
            raise ValueError("review_authority_unavailable") from exc
        except ValidationError as exc:
            raise ValueError("review_authority_invalid") from exc
    return call


class ReadOnlyReviewAuthority(ReviewStore):
    def __init__(self, path: Path):
        self.path = Path(os.path.abspath(path))
        self._identity = self._guard()
        # No super().__init__: it bootstraps and migrates the ledger.
        with self._connect():
            pass

    def _guard(self):
        try:
            for target in (self.path, *self.path.parents,
                           Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
                try:
                    info = target.lstat()
                except FileNotFoundError:
                    if target == self.path:
                        raise ValueError("review_authority_unavailable")
                    continue
                if (stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                    raise ValueError("review_authority_invalid")
            info = self.path.stat()
            identity = (info.st_dev, info.st_ino)
            if not stat.S_ISREG(info.st_mode) or info.st_size < 100:
                raise ValueError("review_authority_invalid")
            if hasattr(self, "_identity") and identity != self._identity:
                raise ValueError("review_authority_invalid")
            with self.path.open("rb") as handle:
                if handle.read(16) != b"SQLite format 3\x00":
                    raise ValueError("review_authority_invalid")
            if (self.path.stat().st_dev, self.path.stat().st_ino) != identity:
                raise ValueError("review_authority_invalid")
            return identity
        except OSError as exc:
            raise ValueError("review_authority_unavailable") from exc

    @staticmethod
    def _schema(connection):
        object_type = connection.execute("SELECT type FROM sqlite_master WHERE name='reviews'").fetchone()
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(reviews)")}
        required = {"sequence", "record_id", "source_id", "snapshot", "file_hash",
                    "configuration_version", "payload", "scope", "subject_id"}
        if (object_type != ("table",) or not required.issubset(columns)
                or columns["sequence"][2].upper() != "INTEGER" or columns["sequence"][5] != 1
                or any(columns[name][2].upper() != "TEXT" or columns[name][3] != 1
                       for name in required - {"sequence"})):
            raise ValueError("review_authority_invalid")
        if connection.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]:
            raise ValueError("review_authority_invalid")

    @staticmethod
    def _close(connection, *, rollback=False):
        try:
            try:
                if rollback and connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise _sqlite_failure(exc) from exc

    @contextmanager
    def _connect(self):
        connection = None
        try:
            self._guard()
            # mode=ro prevents creating the missing main database even if loss
            # happens between the filesystem guard and sqlite3.connect.
            connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.2)
            connection.execute("PRAGMA query_only=ON")
            self._guard()
            self._schema(connection)
            with connection:
                yield connection
                self._guard()
        except sqlite3.Error as exc:
            raise _sqlite_failure(exc) from exc
        except OSError as exc:
            raise ValueError("review_authority_unavailable") from exc
        finally:
            if connection is not None:
                self._close(connection)

    @_controlled_read
    def latest(self, **kwargs):
        return super().latest(**kwargs)

    @_controlled_read
    def require_current(self, **kwargs):
        return super().require_current(**kwargs)

    @_controlled_read
    def history(self, source_id):
        return super().history(source_id)

    @_controlled_read
    def state_digest(self):
        try:
            return super().state_digest()
        except ValueError as exc:
            if str(exc).startswith("review_authority_"):
                raise
            raise ValueError("review_authority_invalid") from exc

    @_controlled_read
    def relation_state_digest(self):
        try:
            return super().relation_state_digest()
        except ValueError as exc:
            if str(exc).startswith("review_authority_"):
                raise
            raise ValueError("review_authority_invalid") from exc

    def record(self, *args, **kwargs):
        raise ValueError("review_authority_read_only")

    def import_csv(self, *args, **kwargs):
        raise ValueError("review_authority_read_only")

    def export_csv(self, *args, **kwargs):
        raise ValueError("review_authority_read_only")

    @contextmanager
    def hold_current(self):
        """Reserve current heads for a short final publication boundary only."""
        connection = None
        try:
            self._guard()
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=2)
            self._guard()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("PRAGMA query_only=ON")
            self._schema(connection)
            yield None
            self._guard()
        except sqlite3.Error as exc:
            raise _sqlite_failure(exc) from exc
        except OSError as exc:
            raise ValueError("review_authority_unavailable") from exc
        finally:
            if connection is not None:
                self._close(connection, rollback=True)
