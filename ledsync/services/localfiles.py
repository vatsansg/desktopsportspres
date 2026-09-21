"""Writing and removing downloaded files safely inside ONE dedicated folder.

The file names come from cloud storage, which other people control, so every write is confined:
  * the folder itself must not be an operating-system folder, a drive root, the application's own data
    folder or any folder that CONTAINS it (a file named `ledsync.db` must never be able to replace the
    database). These are decided by file IDENTITY (`os.path.samefile`), not by spelling, so a mapped
    drive, `\\\\localhost\\C$` alias, junction or short name cannot hide them;
  * a name is ONE plain file name (validated exactly like a change-log path segment: no `:` stream,
    no device names, no 8.3 alias, no trailing dot or space, no separators) and not a type that can run
    code or make Windows contact another computer;
  * the target must be a plain file or absent - never a folder, a symbolic link or a junction;
  * data is written to a temporary file in the same folder, flushed to disk and then moved into place,
    so a crash, power cut or full disk never leaves half a file under the real name; stale temporary
    files from an earlier crash are removed;
  * anything that touches the folder runs under a time limit, so an unreachable network folder cannot
    freeze the application.
Removal deletes only a plain file with such a name in that folder.
"""

import os
import stat
import threading
import time
import uuid
from pathlib import Path

from . import mappings
from .changelog import _path_problem, blocked_name_problem

REPARSE_POINT = 0x400        # FILE_ATTRIBUTE_REPARSE_POINT: junctions and symbolic links
TEMP_SUFFIX = ".ledsync-tmp"
STALE_TEMP_SECONDS = 3600
FOLDER_TIMEOUT = 10.0
FILE_TIMEOUT = 60.0
_OS_ROOT_VARIABLES = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")
_PROTECTED_FILES = ("ledsync.db", "ledsync.db-wal", "ledsync.db-shm", "_localchangelog.csv")


class LocalFileError(Exception):
    """A file could not be written or removed; the message is plain text that is safe to show."""


def run_limited(function, seconds: float = FOLDER_TIMEOUT):
    """Run `function` with a deadline. An unreachable network path can block Windows for over half a
    minute; the check runs on a daemon thread so the application never waits longer than `seconds`."""
    box: dict = {}

    def work():
        try:
            box["value"] = function()
        except BaseException as err:                     # noqa: BLE001 - handed back to the caller below
            box["error"] = err

    thread = threading.Thread(target=work, name="local-folder", daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise LocalFileError("The folder did not respond in time. Check that it is available.")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _is_link(path: Path) -> bool:
    info = os.lstat(path)
    try:
        attributes = info.st_file_attributes           # Windows only
    except AttributeError:
        attributes = 0
    return stat.S_ISLNK(info.st_mode) or bool(attributes & REPARSE_POINT)


def _same(a, b) -> bool:
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def _unsafe_root(root: Path, real: Path, data_dir) -> bool:
    data = Path(data_dir)
    if real.parent == real:                                            # a drive root
        return True
    for folder in (data, *data.parents):                               # the data folder, or a folder that contains it
        if _same(root, folder):
            return True
    for name in _PROTECTED_FILES:                                      # the database must not be reachable as a plain file here
        mine, theirs = root / name, data / name
        if os.path.lexists(mine) and os.path.lexists(theirs) and _same(mine, theirs):
            return True
    system = [Path(v) for v in (os.environ.get(n) for n in _OS_ROOT_VARIABLES) if v]
    for candidate in (root, *root.parents, real, *real.parents):       # an operating-system folder or anything inside one
        if any(_same(candidate, folder) for folder in system):
            return True
    return mappings.is_protected(str(real), ())


def _open_root(root: Path, data_dir) -> Path:
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise LocalFileError("The folder could not be created. Check the folder in Settings.") from None
    real = Path(os.path.realpath(root))
    if not real.is_dir():
        raise LocalFileError("That path is not a folder. Check the folder in Settings.")
    if _unsafe_root(root, real, data_dir):
        raise LocalFileError("That folder is reserved for the operating system or this application. "
                             "Choose another folder in Settings.")
    _sweep_stale_temporaries(real)
    return real


def open_root(root, data_dir) -> Path:
    """Create the folder if needed and return it, after refusing an unsafe one (time-limited)."""
    return run_limited(lambda: _open_root(Path(root), data_dir), FOLDER_TIMEOUT)


def _sweep_stale_temporaries(root: Path) -> None:
    """Remove temporary files left by an earlier crash (never one that is still being written)."""
    cutoff = time.time() - STALE_TEMP_SECONDS
    try:
        for entry in os.scandir(root):
            if entry.name.endswith(TEMP_SUFFIX) and entry.is_file(follow_symlinks=False) and entry.stat().st_mtime < cutoff:
                _quiet_remove(Path(entry.path))
    except OSError:
        pass


def existing_names(root, names) -> set | None:
    """Which of `names` exist as files in `root` (None if the folder does not answer in time)."""
    def look():
        return {n for n in names if os.path.lexists(Path(root) / n)}
    try:
        return run_limited(look, 5.0)
    except LocalFileError:
        return None


def _target(root: Path, name: str) -> Path:
    if _path_problem(name) or blocked_name_problem(name) or "/" in name or name in (".", ".."):
        raise LocalFileError("The file name cannot be used as a file on this computer.")
    return root / name


def write_atomic(root, name: str, data: bytes) -> Path:
    """Write `data` as `name` inside `root`. Returns the file's path."""
    root = Path(root)
    target = _target(root, name)
    return run_limited(lambda: _write(root, target, data), FILE_TIMEOUT)


def _write(root: Path, target: Path, data: bytes) -> Path:
    temp = root / f".{uuid.uuid4().hex}{TEMP_SUFFIX}"
    try:
        if os.path.lexists(target) and (target.is_dir() or _is_link(target)):
            raise LocalFileError("A folder or link already has that name, so the file was not written.")
        with open(temp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())                                  # on disk before it takes the real name
        os.replace(temp, target)
        landed = os.path.normcase(str(Path(os.path.realpath(target)).parent))
        if landed != os.path.normcase(os.path.realpath(root)):        # defence in depth; a replace never follows a link
            raise LocalFileError("The file did not land in its folder, so it cannot be trusted.")
    except LocalFileError:
        _quiet_remove(temp)
        raise
    except PermissionError:
        _quiet_remove(temp)
        raise LocalFileError("The file could not be replaced. It may be read-only or open in another program.") from None
    except OSError:
        _quiet_remove(temp)
        raise LocalFileError("The file could not be written. Check the folder and free disk space.") from None
    return target


def delete_file(root, name: str) -> bool:
    """Delete a plain file named `name` in `root`. Returns False if there was nothing to delete."""
    root = Path(root)
    target = _target(root, name)
    return run_limited(lambda: _delete(target), FILE_TIMEOUT)


def _delete(target: Path) -> bool:
    try:
        if not os.path.lexists(target):
            return False
        if target.is_dir() or _is_link(target):
            raise LocalFileError("A folder or link has that name, so nothing was deleted.")
        os.remove(target)
        return True
    except LocalFileError:
        raise
    except OSError:
        raise LocalFileError("The file could not be deleted. It may be read-only or open in another program.") from None


def _quiet_remove(path: Path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
