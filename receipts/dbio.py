"""Read-only access to the Hermes session store (``state.db``).

Every path in this module opens SQLite with ``mode=ro``.  The live database is
owned by the running Hermes process; Receipts must never take a write lock, run
a migration, or create a rollback journal next to it.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

__all__ = [
    "ReceiptsError",
    "hermes_home",
    "state_db_path",
    "open_ro",
    "db_identity",
]


class ReceiptsError(RuntimeError):
    """A user-facing failure (bad target, missing store, no matching session)."""


def hermes_home() -> Path:
    """Resolve the active Hermes home, profile-aware.

    Order: ``$HERMES_HOME`` (set by the runtime per profile) -> the core
    resolver -> the platform default.  Never hardcode ``~/.hermes`` as *the*
    home; a profile keeps its state somewhere else entirely.
    """
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    try:  # core resolver, when running inside Hermes
        from hermes_constants import get_hermes_home  # type: ignore

        resolved = get_hermes_home()
        if resolved:
            return Path(resolved)
    except Exception:
        pass
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "hermes"
    return Path.home() / ".hermes"


def state_db_path() -> Path:
    """Path to the session store for the active home."""
    explicit = os.environ.get("RECEIPTS_DB")
    if explicit:
        return Path(explicit).expanduser()
    return hermes_home() / "state.db"


def _connect_ro(db_path: Path) -> tuple[sqlite3.Connection, Path | None]:
    """Open read-only, falling back to a scratch snapshot.

    Returns ``(connection, scratch_dir)``.  Connecting is kept separate from
    yielding on purpose: a context manager that yielded inside its own retry loop
    would yield twice if the caller's exception were mistaken for a connect
    failure.
    """
    last: Exception | None = None
    for uri in (f"file:{db_path}?mode=ro", f"file:{db_path.as_posix()}?mode=ro"):
        conn = None
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = 1")
            conn.execute("PRAGMA busy_timeout = 15000")
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            return conn, None
        except sqlite3.Error as exc:  # pragma: no cover - depends on host FS
            last = exc
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    # pragma: no cover - depends on host FS
    scratch = Path(tempfile.mkdtemp(prefix="receipts-"))
    try:
        copy = scratch / "state.db"
        shutil.copy2(db_path, copy)
        for suffix in ("-wal", "-shm"):
            side = db_path.with_name(db_path.name + suffix)
            if side.exists():
                try:
                    shutil.copy2(side, copy.with_name(copy.name + suffix))
                except OSError:
                    pass
        conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = 1")
        return conn, scratch
    except (sqlite3.Error, OSError) as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        raise ReceiptsError(
            f"Could not read {db_path} read-only ({last or exc}). "
            "Close Hermes or copy the database yourself and pass --db."
        ) from exc


@contextmanager
def open_ro(path: Path | None = None):
    """Open the session store strictly read-only."""
    db_path = Path(path) if path is not None else state_db_path()
    if not db_path.exists():
        raise ReceiptsError(
            f"No Hermes session store at {db_path}. "
            "Run Hermes at least once, or pass --db / set RECEIPTS_DB."
        )
    if not db_path.is_file():
        raise ReceiptsError(f"{db_path} is not a file.")

    conn, scratch = _connect_ro(db_path)
    try:
        yield conn
    finally:
        try:
            conn.close()
        finally:
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)


def db_identity(path: Path | None = None) -> dict:
    """Provenance for the artifact footer: which store, how big, when written."""
    db_path = Path(path) if path is not None else state_db_path()
    info = {"path": str(db_path), "exists": db_path.exists()}
    try:
        stat = db_path.stat()
        info["size_bytes"] = stat.st_size
        info["mtime"] = stat.st_mtime
    except OSError:
        info["size_bytes"] = None
        info["mtime"] = None
    return info
