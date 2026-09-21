"""Read-only access to the web application's Azure Storage account (BRD Sections 4, 15, 16).

THIS APPLICATION ONLY EVER READS FROM AZURE (BRD 4, Business Rule 13): it must never write,
rename or delete anything in the web application's storage. That is enforced by code discipline
and by tests: this module exposes only listing and downloading, and an AST-based test
(tests/test_storage.py) forbids mutating SDK calls, private SDK internals, dynamic attribute
access and any other HTTP client anywhere under ledsync/. NOTE: a Storage Account access key is
inherently all-powerful - Azure itself would allow writes with it - so read-only here is a rule
the application keeps, not something the key enforces.

Every Azure failure is turned into a `StorageError` with a BRD Section 21 category and a
plain-language message. Exception text from the SDK is NEVER shown or logged (it can contain
request URLs and identifiers), and the access key never appears in any message.
"""

import logging
import re
from dataclasses import dataclass
from datetime import timezone
from urllib.parse import quote

from azure.core.exceptions import (
    ClientAuthenticationError, DecodeError, HttpResponseError, ResourceNotFoundError, ServiceRequestError,
    ServiceResponseError,
)
from azure.storage.blob import BlobServiceClient, ExponentialRetry

from . import exceptions

log = logging.getLogger("ledsync.storage")

CONNECT_TIMEOUT = 8          # seconds to establish a connection
READ_TIMEOUT = 20            # seconds to wait for data
RETRY_TOTAL = 1              # one retry: an offline venue must fail in seconds, not minutes
YEAR_CONTAINER_RE = re.compile(r"[0-9]{4}")
MAX_TOP_LEVEL_FOLDERS = 20000
_UNSAFE_PATH = re.compile(r"[\x00-\x1f\x7f\\]")
_ASCII_LOWER = {ord(c): ord(c) + 32 for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}


class StorageError(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass(frozen=True)
class EventLocation:
    container: str
    folder: str            # the event folder name, without slashes: '1000 - Star contender Doha'

    def blob_path(self, relative_path: str) -> str:
        """Full blob name for a path RELATIVE to the event folder. Relative paths can come from
        untrusted data (later: change-log entries), so anything that could leave the folder or
        confuse a path is refused."""
        parts = relative_path.split("/")
        if (not relative_path or relative_path.startswith("/") or _UNSAFE_PATH.search(relative_path)
                or any(p in ("", ".", "..") for p in parts) or len(relative_path) > 1024):
            raise StorageError(exceptions.CONFIGURATION, "A file path in the event data is not valid, so it was not used.")
        return f"{self.folder}/{relative_path}"

    @property
    def guid_blob_path(self) -> str:
        return self.blob_path("_GUID.json")

    def blob_url(self, account: str, relative_path: str) -> str:
        """Address of a blob, for the record. Never contains a credential."""
        return (f"https://{account}.blob.core.windows.net/{quote(self.container)}/"
                f"{quote(self.folder)}/{quote(relative_path)}")


@dataclass(frozen=True)
class BlobInfo:
    """A file in an event folder as Azure lists it (real spelling, size and fingerprint)."""
    path: str                  # relative to the event folder, e.g. 'rpi/HOME_Look.png'
    size: int | None
    md5: bytes | None          # Azure's own MD5 of the content, when it has one
    modified: str              # last-modified time, ISO UTC ('' if unknown)


@dataclass(frozen=True)
class ConnectionReport:
    account: str
    containers: tuple[str, ...]          # all containers visible to the key
    year_containers: tuple[str, ...]
    preferred_container_found: bool | None   # None when no container was specified


def map_error(exc: Exception, doing: str) -> StorageError:
    """Translate an SDK/network failure into a safe, categorised StorageError."""
    if isinstance(exc, StorageError):
        return exc
    if isinstance(exc, ClientAuthenticationError) or (
            isinstance(exc, HttpResponseError) and getattr(exc, "status_code", None) in (401, 403)):
        return StorageError(
            exceptions.PERMISSION,
            "Azure refused the storage account key. Check the account name and access key in "
            "Settings, and that the key has not been rotated.")
    if isinstance(exc, ResourceNotFoundError) or (
            isinstance(exc, HttpResponseError) and getattr(exc, "status_code", None) == 404):
        return StorageError(exceptions.MISSING_FOLDER, f"Azure could not find what was asked for while {doing}.")
    if isinstance(exc, DecodeError):
        return StorageError(
            exceptions.DOWNLOAD,
            f"Azure Storage sent an unexpected reply while {doing}. A proxy or a sign-in page on the "
            "network may be interfering.")
    if isinstance(exc, (ServiceRequestError, ServiceResponseError, TimeoutError, ConnectionError, OSError)):
        return StorageError(
            exceptions.STORAGE_CONNECTIVITY,
            "Could not reach Azure Storage. Check the internet connection and the storage account name, then try again.")
    if isinstance(exc, HttpResponseError):
        return StorageError(exceptions.DOWNLOAD,
                            f"Azure Storage returned an error (status {getattr(exc, 'status_code', '?')}) while {doing}.")
    return StorageError(exceptions.DOWNLOAD, f"An unexpected problem occurred while {doing}.")


class AzureReadOnlyStorage:
    """Listing and downloading only. Construct with the account name and key; the key is held
    only by the SDK client and is never exposed by this class."""

    def __init__(self, account: str, access_key: str, *, service_factory=None):
        self._account = account
        factory = service_factory or self._default_service
        self._service = factory(f"https://{account}.blob.core.windows.net", access_key)

    @staticmethod
    def _default_service(account_url: str, access_key: str):
        return BlobServiceClient(
            account_url=account_url, credential=access_key,
            connection_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT,
            retry_policy=ExponentialRetry(initial_backoff=1, increment_base=2, retry_total=RETRY_TOTAL,
                                          random_jitter_range=1),
        )

    @property
    def account(self) -> str:
        return self._account

    # --- connection test ---------------------------------------------------------------
    def test_connection(self, preferred_container: str = "") -> ConnectionReport:
        try:
            names = tuple(sorted(c.name if hasattr(c, "name") else str(c)
                                 for c in self._service.list_containers()))
        except Exception as exc:                                  # noqa: BLE001 - mapped below
            raise map_error(exc, "connecting to Azure Storage") from None
        years = tuple(n for n in names if YEAR_CONTAINER_RE.fullmatch(n))
        found = None if not preferred_container else preferred_container in names
        return ConnectionReport(self._account, names, years, found)

    # --- finding an event ----------------------------------------------------------------
    def _folders_for_event(self, container: str, event_id: str) -> list[str]:
        """Top-level folders '<EventID> - ...' compared CASE-INSENSITIVELY (Event IDs are
        case-insensitive; Azure's own prefix filter is not, so the top level is scanned)."""
        wanted = f"{event_id} - ".casefold()
        client = self._service.get_container_client(container)
        folders, scanned = [], 0
        for item in client.walk_blobs(delimiter="/"):
            scanned += 1
            if scanned > MAX_TOP_LEVEL_FOLDERS:
                raise StorageError(exceptions.CONFIGURATION,
                                   f"Container {container} holds too many folders to search for an event.")
            name = getattr(item, "name", "")
            if name.endswith("/") and name.casefold().startswith(wanted):
                folders.append(name[:-1])
        return folders

    def _year_containers_newest_first(self, exclude: str) -> list[str]:
        names = [c.name if hasattr(c, "name") else str(c) for c in self._service.list_containers()]
        return sorted((n for n in names if YEAR_CONTAINER_RE.fullmatch(n) and n != exclude), reverse=True)

    def _containers_to_search(self, preferred_container: str):
        """The preferred container first, then the other year containers (listed lazily, so
        the extra call is only made when the preferred container does not have the event)."""
        if preferred_container:
            yield preferred_container
        yield from self._year_containers_newest_first(preferred_container)

    def find_event(self, event_id: str, preferred_container: str = "") -> EventLocation:
        """Find the folder '<EventID> - <name>' - in the preferred container first, then the
        other year containers, newest first."""
        try:
            for container in self._containers_to_search(preferred_container):
                found = self._search(container, event_id)
                if found:
                    return found
        except StorageError:
            raise
        except Exception as exc:                                  # noqa: BLE001
            raise map_error(exc, f"looking for event {event_id}") from None
        where = f" in container {preferred_container} or the other year containers" if preferred_container else ""
        raise StorageError(
            exceptions.MISSING_FOLDER,
            f"No folder for event {event_id} was found{where}. Check the Event ID and the container in Settings.")

    def _search(self, container: str, event_id: str) -> EventLocation | None:
        try:
            folders = self._folders_for_event(container, event_id)
        except ResourceNotFoundError:
            return None                                           # that container does not exist: keep looking
        except HttpResponseError as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            raise
        if not folders:
            return None
        if len(folders) > 1:
            raise StorageError(
                exceptions.CONFIGURATION,
                f"More than one folder in container {container} starts with '{event_id} - '. "
                "The event cannot be identified; ask the web application administrator to check the storage.")
        return EventLocation(container, folders[0])

    # --- locating a file whose path is written in another letter case ----------------------------
    def resolve_relative_path(self, location: EventLocation, relative_path: str, cache: dict | None = None) -> str:
        """The REAL path (inside the event folder) of a file the change log names in any A-Z letter
        case: the log says `RPI/HOME_Look.png` while Azure holds `rpi/HOME_Look.png`, and Azure blob
        names are case-sensitive. Each level is looked up by listing (a read), never guessed.
        `cache` (one dict per run) keeps each folder's listing so many files cost few calls.
        Raises StorageError if nothing matches or if two different files match (ambiguous)."""
        return self.find_blob(location, relative_path, cache).path

    def find_blob(self, location: EventLocation, relative_path: str, cache: dict | None = None) -> BlobInfo:
        """Like `resolve_relative_path`, but also returns the size and fingerprint Azure lists for the file."""
        location.blob_path(relative_path)                              # validates the path
        wanted = relative_path.split("/")
        cache = {} if cache is None else cache
        frontier = [(location.folder + "/", None)]                    # every real spelling that fits so far
        try:
            for depth, part in enumerate(wanted):
                is_file = depth == len(wanted) - 1
                folded = part.translate(_ASCII_LOWER)
                found = []
                for prefix, _ in frontier:
                    for entry in self._listing(location.container, prefix, cache):
                        if entry[1] == is_file and entry[0].translate(_ASCII_LOWER) == folded:
                            found.append((prefix + entry[0] + ("" if is_file else "/"), entry))
                if not found:
                    raise StorageError(exceptions.MISSING_FOLDER,
                                       f"The file {relative_path} was not found in the event folder.")
                frontier = found
            if len(frontier) > 1:
                raise StorageError(exceptions.CONFIGURATION,
                                   f"More than one file in the event folder matches {relative_path} apart from "
                                   "letter case, so it was not used. Ask the web application administrator to rename one.")
        except StorageError:
            raise
        except Exception as exc:                                       # noqa: BLE001
            raise map_error(exc, f"looking for {relative_path}") from None
        full, entry = frontier[0]
        return BlobInfo(full[len(location.folder) + 1:], entry[2], entry[3], entry[4])

    def list_files(self, location: EventLocation, folder_path: str, cache: dict | None = None) -> list[BlobInfo]:
        """The files directly inside a folder of the event (e.g. `Table 1/Inner`), the folder being matched in any
        A-Z letter case. A folder that does not exist has no files. Read-only listing."""
        location.blob_path(folder_path + "/x")                         # validates the folder path
        cache = {} if cache is None else cache
        frontier = [location.folder + "/"]
        try:
            for part in folder_path.split("/"):
                folded = part.translate(_ASCII_LOWER)
                frontier = [prefix + entry[0] + "/" for prefix in frontier
                            for entry in self._listing(location.container, prefix, cache)
                            if not entry[1] and entry[0].translate(_ASCII_LOWER) == folded]
                if not frontier:
                    return []
            if len(frontier) > 1:
                raise StorageError(exceptions.CONFIGURATION,
                                   f"More than one folder in the event folder matches {folder_path} apart from letter "
                                   "case, so it was not used. Ask the web application administrator to rename one.")
            return [BlobInfo(frontier[0][len(location.folder) + 1:] + entry[0], entry[2], entry[3], entry[4])
                    for entry in self._listing(location.container, frontier[0], cache) if entry[1]]
        except StorageError:
            raise
        except Exception as exc:                                       # noqa: BLE001
            raise map_error(exc, f"listing {folder_path}") from None

    def _listing(self, container: str, prefix: str, cache: dict) -> list:
        """(name, is_file, size, md5, modified) for everything directly inside `prefix`, listed once per cache.
        NOTE: `is_file` is the second field; folders have is_file False."""
        key = (container, prefix)
        if key not in cache:
            entries = []
            for item in self._service.get_container_client(container).walk_blobs(name_starts_with=prefix, delimiter="/"):
                name = item.name
                if not name.startswith(prefix):
                    continue
                leaf = name[len(prefix):]
                folder = leaf.endswith("/")
                leaf = leaf[:-1] if folder else leaf
                if not leaf or "/" in leaf:
                    continue
                size = md5 = None
                modified = ""
                if not folder:
                    try:
                        size = int(item.size)
                    except (AttributeError, TypeError, ValueError):
                        size = None
                    try:
                        raw = item.content_settings.content_md5
                        md5 = bytes(raw) if raw else None
                    except (AttributeError, TypeError):
                        md5 = None
                    try:
                        modified = item.last_modified.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except (AttributeError, TypeError, ValueError):
                        modified = ""
                entries.append((leaf, not folder, size, md5, modified))
            cache[key] = entries
        return cache[key]

    def iter_blob(self, location: EventLocation, relative_path: str, size: int, chunk_size: int = 4 * 1024 * 1024):
        """Yield a file's bytes in ranges of `chunk_size` (so a large video never sits in memory whole). `relative_path`
        must be the REAL path from `find_blob`, `size` the listed size. Raises StorageError."""
        blob = location.blob_path(relative_path)
        offset = 0
        try:
            client = self._service.get_blob_client(container=location.container, blob=blob)
            while offset < size:
                data = bytes(client.download_blob(offset=offset, length=min(chunk_size, size - offset)).readall())
                if not data:
                    raise StorageError(exceptions.DOWNLOAD, "Azure ended the download early.")
                offset += len(data)
                yield data
        except StorageError:
            raise
        except ResourceNotFoundError:
            raise StorageError(exceptions.MISSING_FOLDER, f"The file {relative_path} was not found in the event folder.") from None
        except Exception as exc:                                       # noqa: BLE001
            raise map_error(exc, f"downloading {relative_path}") from None

    # --- downloading -----------------------------------------------------------------------------
    def read_blob(self, location: EventLocation, relative_path: str, max_bytes: int) -> bytes:
        """Download one file from an event folder, refusing to hold more than `max_bytes`
        (asks for +1 so an overflow is detectable). `relative_path` is validated."""
        blob = location.blob_path(relative_path)
        try:
            client = self._service.get_blob_client(container=location.container, blob=blob)
            data = client.download_blob(offset=0, length=max_bytes + 1).readall()
        except ResourceNotFoundError:
            if relative_path == "_GUID.json":
                raise StorageError(
                    exceptions.MISSING_FOLDER,
                    "The event folder exists but has no _GUID.json. Run Export Event for this event in the "
                    "web application, then try again.") from None
            raise StorageError(exceptions.MISSING_FOLDER,
                               f"The event folder has no file {relative_path}.") from None
        except HttpResponseError as exc:
            if getattr(exc, "status_code", None) == 416:          # an empty blob has no byte range to serve
                return b""
            raise map_error(exc, f"downloading {relative_path}") from None
        except Exception as exc:                                  # noqa: BLE001
            raise map_error(exc, f"downloading {relative_path}") from None
        return bytes(data)


def from_settings(settings) -> "AzureReadOnlyStorage":
    """The real, read-only client for the configured account. Flask-free, so a headless
    scheduled run (Phase 12) can use it. Tests replace the factory with a fake."""
    return AzureReadOnlyStorage(settings.account, settings.access_key)
