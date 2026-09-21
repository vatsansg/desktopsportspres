"""Testing access to a device's shared folder (BRD Section 13).

The test verifies access to the FOLDER itself - not merely to the device (BRD 13). Owner decisions
(21/09/26): no ping is used at all, and write access is proven by creating and immediately deleting
a tiny, uniquely named probe file (the only reliable way to prove write permission on a share).

Safety:
  * Every check runs on a daemon thread with a hard deadline. Windows can block for up to a minute
    on an unreachable network path; the application must not freeze, and a stuck thread must never
    keep the program from exiting.
  * The probe file is created with exclusive creation, so an existing file is never overwritten,
    has a random name that cannot match a real asset, and is removed at once.
  * The application stores no credentials: access is whatever the running Windows account has.
  * Failure messages are fixed plain-language text; Windows error text is never shown.
"""

import os
import stat
import threading
import time
import uuid
from dataclasses import dataclass

from . import exceptions, mappings

DEFAULT_TIMEOUT = 10.0          # seconds allowed for one destination
PROBE_PREFIX = ".ledsync-probe-"

# Windows error numbers (winerror) grouped by what the operator should check.
_NETWORK = {51, 52, 53, 55, 59, 64, 121, 1203, 1225, 1231, 1232}   # remote not listening, path not found, device gone, unreachable, timeouts
_NO_SUCH_SHARE_OR_FOLDER = {2, 3, 67, 123, 161}                   # file/path not found, network NAME not found (share missing), bad name
_TOO_LONG = {206}                                                 # file name or extension too long
_ACCESS = {5, 86, 1219, 1326, 1385, 1909}                         # access denied, bad password, conflicting credentials, logon failure/refused
_READ_ONLY = {19}                                                 # media is write-protected


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str
    category: str | None = None          # a BRD Section 21 category when not ok
    warning: str = ""                    # e.g. the probe file could not be removed

    @property
    def status_text(self) -> str:
        return "Connection Successful" if self.ok else "Connection Failed"


def _fail(category: str, message: str) -> CheckResult:
    return CheckResult(False, message, category)


def _classify(err: OSError, doing: str) -> CheckResult:
    win = getattr(err, "winerror", None)
    if isinstance(err, TimeoutError):
        return _fail(exceptions.NETWORK_DEVICE, "The device did not respond in time. Check that it is on and on the network.")
    if isinstance(err, PermissionError) or win in _ACCESS:
        return _fail(exceptions.PERMISSION,
                     f"Access was denied while {doing}. Check that this Windows account is allowed to use the shared folder.")
    if win in _READ_ONLY:
        return _fail(exceptions.PERMISSION, "The folder is read-only, so files cannot be copied into it.")
    # The Windows error number decides FIRST: Python reports "network path not found" (53) as a
    # FileNotFoundError too, but that means the DEVICE cannot be reached, not that a folder is missing.
    if win in _NETWORK or isinstance(err, ConnectionError):
        return _fail(exceptions.NETWORK_DEVICE,
                     "The device could not be reached. Check the device is on, on the network, and the name is right.")
    if win in _TOO_LONG:
        return _fail(exceptions.CONFIGURATION, "The folder path is too long for Windows. Use a shorter path.")
    if isinstance(err, FileNotFoundError) or win in _NO_SUCH_SHARE_OR_FOLDER:
        return _fail(exceptions.MISSING_FOLDER, "The folder does not exist. Check the shared folder name and path.")
    return _fail(exceptions.FILE_ACCESS, f"The folder could not be used while {doing}.")


def _probe_name() -> str:
    return f"{PROBE_PREFIX}{uuid.uuid4().hex}.tmp"


def _check_folder_blocking(path: str, forbidden_roots: tuple = ()) -> CheckResult:
    """The actual checks. May block for a long time on an unreachable network path."""
    try:
        mode = os.stat(path).st_mode
    except OSError as err:
        return _classify(err, "opening the folder")
    if not stat.S_ISDIR(mode):
        return _fail(exceptions.CONFIGURATION, "That path is a file, not a folder.")
    try:
        real = os.path.realpath(path)                 # follows junctions and expands 8.3 names
    except (OSError, ValueError):
        real = path
    if mappings.is_protected(real, forbidden_roots):
        return _fail(exceptions.CONFIGURATION, "That folder is reserved for the operating system or this application, "
                                               "so it cannot be used. Choose a dedicated folder for the LED files.")

    try:
        with os.scandir(path) as entries:                  # can the folder be listed?
            next(iter(entries), None)
    except OSError as err:
        return _classify(err, "reading the folder")

    probe = os.path.join(path, _probe_name())
    try:
        with open(probe, "xb"):                             # 'x': never overwrite an existing file
            pass
    except OSError as err:
        return _classify(err, "writing to the folder")
    try:
        os.remove(probe)
    except OSError:
        return CheckResult(True, "The folder can be opened and written to.", None,
                           warning="A small empty test file could not be removed from the folder "
                                   f"({os.path.basename(probe)}). It is harmless and can be deleted.")
    return CheckResult(True, "The folder can be opened and written to.")


def check_folder(path: str, timeout: float = DEFAULT_TIMEOUT, _check=None, forbidden_roots: tuple = ()) -> CheckResult:
    """Run the check with a hard deadline. Never raises."""
    return check_many({"only": path}, timeout, _check, forbidden_roots)["only"]


def check_many(paths: dict, timeout: float = DEFAULT_TIMEOUT, _check=None, forbidden_roots: tuple = ()) -> dict:
    """Check several destinations IN PARALLEL under one shared deadline (so 'Test All' on a venue with
    a dead device takes ~`timeout` seconds, not `timeout` x N). `paths` maps a key to a folder path."""
    check = _check or (lambda p: _check_folder_blocking(p, forbidden_roots))
    results: dict = {}
    lock = threading.Lock()

    def work(key, path):
        try:
            outcome = check(path)
        except Exception:                                    # noqa: BLE001 - one bad destination must not break the rest
            outcome = _fail(exceptions.FILE_ACCESS, "The folder could not be tested because of an unexpected problem.")
        with lock:
            results[key] = outcome

    threads = {}
    for key, path in paths.items():
        t = threading.Thread(target=work, args=(key, path), name=f"folder-check-{key}", daemon=True)
        threads[key] = t
        t.start()
    deadline = time.monotonic() + timeout
    for t in threads.values():
        t.join(max(0.0, deadline - time.monotonic()))
    with lock:
        return {key: results.get(key) or _fail(
            exceptions.NETWORK_DEVICE,
            f"The device did not respond within {int(timeout)} seconds. Check that it is on and on the network.")
            for key in paths}
