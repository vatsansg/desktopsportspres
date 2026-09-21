"""Writing and removing downloaded files safely inside ONE dedicated folder.

The file names come from cloud storage, which other people control, so every write is confined:
  * the folder itself must not be an operating-system folder or the application's own data folder
    (a file named `ledsync.db` must never be able to replace the database);
  * a name is ONE plain file name (validated exactly like a change-log path segment: no `:` stream,
    no device names, no trailing dot or space, no path separators);
  * the target must be a plain file or absent - never a folder, a symbolic link or a junction;
  * data is written to a temporary file in the same folder and then moved into place, so a crash
    or a full disk never leaves half a file under the real name;
  * after writing, the real location is re-checked to be inside the folder.
Removal deletes only a plain file with such a name in that folder.
"""

import os
import stat
import uuid
from pathlib import Path

from . import mappings
from .changelog import _path_problem

REPARSE_POINT = 0x400        # FILE_ATTRIBUTE_REPARSE_POINT: junctions and symbolic links


class LocalFileError(Exception):
    """A file could not be written or removed; the message is plain text that is safe to show."""


def _is_link(path: Path) -> bool:
    info = os.lstat(path)
    try:
        attributes = info.st_file_attributes           # Windows only
    except AttributeError:
        attributes = 0
    return stat.S_ISLNK(info.st_mode) or bool(attributes & REPARSE_POINT)


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def open_root(root, data_dir) -> Path:
    """Create the folder if needed and return it, after refusing an unsafe one."""
    root = Path(root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise LocalFileError("The folder could not be created. Check the folder in Settings.") from None
    real = Path(os.path.realpath(root))
    if not real.is_dir():
        raise LocalFileError("That path is not a folder. Check the folder in Settings.")
    if _same(real, Path(data_dir)) or mappings.is_protected(str(real), ()):
        raise LocalFileError("That folder is reserved for the operating system or this application. "
                             "Choose another folder in Settings.")
    return real


def _target(root: Path, name: str) -> Path:
    problem = _path_problem(name)
    if problem or "/" in name or name in (".", ".."):
        raise LocalFileError("The file name cannot be used as a file on this computer.")
    return root / name


def write_atomic(root, name: str, data: bytes) -> Path:
    """Write `data` as `name` inside `root`. Returns the file's path."""
    root = Path(root)
    target = _target(root, name)
    temp = root / f".{uuid.uuid4().hex}.ledsync-tmp"
    try:
        if os.path.lexists(target) and (target.is_dir() or _is_link(target)):
            raise LocalFileError("A folder or link already has that name, so the file was not written.")
        with open(temp, "xb") as handle:
            handle.write(data)
        os.replace(temp, target)
        landed = os.path.normcase(str(Path(os.path.realpath(target)).parent))
        if landed != os.path.normcase(os.path.realpath(root)):        # defence in depth; a replace never follows a link
            raise LocalFileError("The file did not land in its folder, so it cannot be trusted.")
    except LocalFileError:
        _quiet_remove(temp)
        raise
    except OSError:
        _quiet_remove(temp)
        raise LocalFileError("The file could not be written. Check the folder and free disk space.") from None
    return target


def delete_file(root, name: str) -> bool:
    """Delete a plain file named `name` in `root`. Returns False if there was nothing to delete."""
    target = _target(Path(root), name)
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
        raise LocalFileError("The file could not be deleted. It may be open in another program.") from None


def _quiet_remove(path: Path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
