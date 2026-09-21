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

import hashlib
import os
import re
import shutil
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
_TEMP_NAME = re.compile(r"^\.[0-9a-f]{32}" + re.escape(TEMP_SUFFIX) + "$")       # exactly what this application creates
_swept: dict = {}                                                                # folder -> when it was last swept
MAX_FOLDER_PATH = 200                                                            # leaves room for a file name (Windows: 260)
MAX_FILE_PATH = 250
FOLDER_TIMEOUT = 10.0
FILE_TIMEOUT = 60.0
_OS_ROOT_VARIABLES = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")
_PROTECTED_FILES = ("ledsync.db", "ledsync.db-wal", "ledsync.db-shm", "_localchangelog.csv")


class LocalFileError(Exception):
    """A file could not be written or removed; the message is plain text that is safe to show."""


class DeviceError(LocalFileError):
    """A device (shared) folder could not be used. `category` is the BRD Section 21 category for the exception log."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


class IntegrityError(LocalFileError):
    """The downloaded bytes are not what Azure listed (wrong size or checksum); the file was discarded."""


class Cancelled(Exception):
    """The operator cancelled the run."""


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


def _sweep_stale_temporaries(folder: Path) -> None:
    """Remove temporary files this application left in `folder` after a crash or a closed window (never a fresh one, and
    only names of exactly the form it creates - a user's own file is never touched). At most once per 10 minutes."""
    key, now = os.path.normcase(str(folder)), time.time()
    if now - _swept.get(key, 0) < 600:
        return
    _swept[key] = now
    cutoff = now - STALE_TEMP_SECONDS
    try:
        for entry in os.scandir(folder):
            if _TEMP_NAME.match(entry.name) and entry.is_file(follow_symlinks=False) and entry.stat().st_mtime < cutoff:
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
    if len(str(root / name)) > MAX_FILE_PATH:
        raise LocalFileError("The path to this file would be too long for Windows. Choose a shorter asset or RPI folder in Settings.")
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


# --- folders below a root, created by this application from names it controls ---------------------------------

def _folder_name_problem(name: str) -> bool:
    return bool(_path_problem(name)) or "/" in name or "\\" in name or name in (".", "..")


def subfolder_path(root, *names) -> Path:
    """The path of `root\\names[0]\\names[1]...` (each a plain, safe folder name) WITHOUT touching the disk."""
    path = Path(root)
    for name in names:
        if _folder_name_problem(name):
            raise LocalFileError("A folder name cannot be used on this computer.")
        path = path / name
    if len(str(path)) > MAX_FOLDER_PATH:
        raise LocalFileError("The folder path would be too long for Windows. Choose a shorter asset or RPI folder in Settings.")
    return path


def open_subfolder(root, *names) -> Path:
    """Create (if needed) and return `root\\names...`. Every level must be a plain folder: an existing file, link or
    junction with that name is refused, so nothing can be redirected somewhere else."""
    subfolder_path(root, *names)                                   # validates every name before anything is created
    return run_limited(lambda: _make_folders(Path(root), names), FOLDER_TIMEOUT)


def existing_subfolder(root, *names) -> Path | None:
    """`root\\names...` only if it already exists as plain folders all the way down (None if it does not). A link or
    junction at any level is refused, exactly as when creating - so a removal can never be steered elsewhere."""
    path = subfolder_path(root, *names)

    def walk():
        here = Path(root)
        for name in names:
            here = here / name
            if not os.path.lexists(here):
                return None
            if _is_link(here) or not here.is_dir():
                raise LocalFileError("A file or link has the name of a folder this application needs, so it was not used.")
        return path
    return run_limited(walk, FOLDER_TIMEOUT)


def _make_folders(root: Path, names) -> Path:
    path = root
    for name in names:
        path = path / name
        try:
            if os.path.lexists(path):
                if _is_link(path) or not path.is_dir():
                    raise LocalFileError("A file or link has the name of a folder this application needs, so it was not used.")
            else:
                os.mkdir(path)
        except LocalFileError:
            raise
        except OSError:
            raise LocalFileError("A folder could not be created. Check the folder and free disk space.") from None
    _sweep_stale_temporaries(path)
    return path


# --- streaming a download to disk, verified -------------------------------------------------------------------

def write_stream(root, name: str, chunks, *, size: int | None, md5: bytes | None, on_bytes=None, cancelled=None) -> Path:
    """Write the bytes from `chunks` as `name` in `root`, never holding the whole file in memory.

    The file is built under a temporary name and only moved into place after it has been checked against what Azure
    listed: exactly `size` bytes and, when Azure has one, the same MD5. A wrong download is discarded (IntegrityError)
    and the previous copy of the file, if any, is left alone. `cancelled()` is asked between chunks."""
    root = Path(root)
    target = _target(root, name)
    if size is not None:
        try:
            free = run_limited(lambda: shutil.disk_usage(root).free, 10.0)
        except (OSError, LocalFileError):
            free = None
        if free is not None and free < size + 64 * 1024 * 1024:
            raise LocalFileError("There is not enough free disk space for this file.")
    temp = root / f".{uuid.uuid4().hex}{TEMP_SUFFIX}"
    try:
        if os.path.lexists(target) and (target.is_dir() or _is_link(target)):
            raise LocalFileError("A folder or link already has that name, so the file was not written.")
        digest, count = hashlib.md5(usedforsecurity=False), 0
        with open(temp, "xb") as handle:
            for chunk in chunks:
                if cancelled is not None and cancelled():
                    raise Cancelled()
                handle.write(chunk)
                digest.update(chunk)
                count += len(chunk)
                if on_bytes is not None:
                    on_bytes(len(chunk))
            handle.flush()
            os.fsync(handle.fileno())
        if size is not None and count != size:
            raise IntegrityError("The downloaded file was not the size Azure lists, so it was discarded.")
        if md5 and digest.digest() != md5:
            raise IntegrityError("The downloaded file did not match Azure's checksum, so it was discarded.")
        run_limited(lambda: os.replace(temp, target), 30.0)
        landed = os.path.normcase(str(Path(os.path.realpath(target)).parent))
        if landed != os.path.normcase(os.path.realpath(root)):
            raise LocalFileError("The file did not land in its folder, so it cannot be trusted.")
    except (LocalFileError, Cancelled):
        _quiet_remove(temp)
        raise
    except PermissionError:
        _quiet_remove(temp)
        raise LocalFileError("The file could not be replaced. It may be read-only or open in another program.") from None
    except OSError:
        _quiet_remove(temp)
        raise LocalFileError("The file could not be written. Check the folder and free disk space.") from None
    except BaseException:
        _quiet_remove(temp)                                        # e.g. a StorageError raised by the download itself
        raise
    return target


def missing_files(folder: Path, names, timeout: float = 5.0) -> set | None:
    """Which of `names` are NOT files in `folder` (None if the folder does not answer in time). A folder that does
    not exist is missing them all."""
    def look():
        return {n for n in names if not os.path.lexists(folder / n)}
    try:
        return run_limited(look, timeout)
    except LocalFileError:
        return None


# --- pushing a file to a LED device's shared folder ---------------------------------------------------------------------

def check_destination(folder, data_dir) -> Path:
    """The device folder must exist, be a plain folder, and not be (or alias) the application's data folder, a folder
    containing it, a drive root or an operating-system folder - judged by file identity. It is NEVER created here."""
    def check():
        path = Path(folder)
        if not path.is_dir():
            raise DeviceError("Missing folder", "The device folder does not exist or is not available.")
        if _unsafe_root(path, Path(os.path.realpath(path)), data_dir):
            raise DeviceError("Configuration", "That device folder is reserved for the operating system or this application.")
        return path
    return run_limited(check, FOLDER_TIMEOUT)


def _device_error(err: OSError, doing: str) -> DeviceError:
    from . import connectivity
    result = connectivity._classify(err, doing)
    return DeviceError(result.category or "File access", result.message)


def push_file(source, dest_dir, name: str, *, on_bytes=None, cancelled=None) -> Path:
    """Copy `source` (a file in the local asset folder) into the device folder as `name`.

    The copy is built under a hidden temporary name on the device, flushed, then READ BACK and compared with the source
    (size and MD5) BEFORE it is renamed into place, so the LED software never sees a half or corrupt file and a wrong copy
    is never left. An existing file of that name is replaced (owner decision). The device folder is never created."""
    source, dest_dir = Path(source), Path(dest_dir)
    target = _target(dest_dir, name)
    try:
        if not source.is_file() or _is_link(source):
            raise IntegrityError("The downloaded file is missing from this computer, so it could not be sent.")
        size = source.stat().st_size
    except OSError:
        raise IntegrityError("The downloaded file could not be read on this computer, so it could not be sent.") from None
    temp = dest_dir / f".{uuid.uuid4().hex}{TEMP_SUFFIX}"
    try:
        if os.path.lexists(target) and (target.is_dir() or _is_link(target)):
            raise LocalFileError("A folder or link on the device already has that name, so the file was not sent.")
        try:
            free = run_limited(lambda: shutil.disk_usage(dest_dir).free, 10.0)
        except (OSError, LocalFileError):
            free = None
        if free is not None and free < size + 16 * 1024 * 1024:
            raise DeviceError("File access", "There is not enough free space on the device folder for this file.")
        source_md5, copied = hashlib.md5(usedforsecurity=False), 0
        with open(source, "rb") as reading, open(temp, "xb") as writing:
            while True:
                if cancelled is not None and cancelled():
                    raise Cancelled()
                chunk = reading.read(4 * 1024 * 1024)
                if not chunk:
                    break
                writing.write(chunk)
                source_md5.update(chunk)
                copied += len(chunk)
                if on_bytes is not None:
                    on_bytes(len(chunk))
            writing.flush()
            try:
                os.fsync(writing.fileno())
            except OSError:
                pass                                                  # some shares cannot flush; the read-back below decides
        if copied != size:
            raise IntegrityError("The file changed on this computer while it was being sent, so it was discarded.")
        # Verify the copy on the device: size and MD5, read back, BEFORE it takes its real name.
        if temp.stat().st_size != size:
            raise IntegrityError("The copy on the device is not the right size, so it was discarded.")
        back = hashlib.md5(usedforsecurity=False)
        with open(temp, "rb") as check:
            while True:
                if cancelled is not None and cancelled():
                    raise Cancelled()
                chunk = check.read(4 * 1024 * 1024)
                if not chunk:
                    break
                back.update(chunk)
        if back.digest() != source_md5.digest():
            raise IntegrityError("The copy on the device did not match the original (checksum), so it was discarded.")
        run_limited(lambda: os.replace(temp, target), 30.0)
    except (LocalFileError, Cancelled):
        _quiet_remove(temp)
        raise
    except PermissionError:
        _quiet_remove(temp)
        raise DeviceError("Permission", "The file on the device could not be replaced. It may be read-only or in use, or this "
                                        "Windows account may not be allowed to write there.") from None
    except OSError as err:
        _quiet_remove(temp)
        raise _device_error(err, "sending the file") from None
    return target
