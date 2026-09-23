"""Cross-process single-instance guard (carried forward since Phase 0/1; closed here because Phase 12
introduces a second process - the headless scheduled run - that can touch the same database and the
same mapped LED folders as an already-open interactive session).

An OS-level advisory lock (`msvcrt.locking`, Windows-only - this application is Windows-only per the
BRD) on one byte of a small file in the application data folder. The lock is held only for the
lifetime of the open file handle and is released automatically by Windows the instant the process
exits, crash included - there is no stale-lock state to detect or clean up, unlike a PID file.

Both the interactive app (`main.py`) and the headless scheduled run (`scheduled_run.py`) take this
same lock before touching the database, so at most one of them - either one - runs at a time.
"""

import contextlib
import logging
from pathlib import Path

import msvcrt

log = logging.getLogger("ledsync.singleinstance")

LOCK_FILENAME = "ledsync.lock"


class AlreadyRunning(Exception):
    """Another instance - the interactive app, or another scheduled run - already holds the lock."""


@contextlib.contextmanager
def instance_lock(data_dir: Path):
    """Hold the single-instance lock for the duration of the `with` block. Raises `AlreadyRunning`
    immediately (never blocks) if another process already holds it."""
    path = Path(data_dir) / LOCK_FILENAME
    if not path.exists():
        path.write_bytes(b"\0")
    fh = open(path, "r+b")
    try:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise AlreadyRunning(
                "Another instance of the application (interactive, or a scheduled run) is already "
                "using this data folder.") from None
        try:
            yield
        finally:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        fh.close()
