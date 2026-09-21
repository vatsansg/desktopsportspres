"""Read-only access to the web application's Azure Storage account (BRD Sections 4, 15, 16).

THIS APPLICATION ONLY EVER READS FROM AZURE (BRD 4, Business Rule 13): it must never write,
rename or delete anything in the web application's storage. That is enforced structurally:
this module exposes only listing and downloading, and a test scans its source for any
mutating SDK call so none can be added unnoticed.

Every Azure failure is turned into a `StorageError` with a BRD Section 21 category and a
plain-language message. Exception text from the SDK is NEVER shown or logged (it can contain
request URLs and identifiers), and the access key never appears in any message.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

from azure.core.exceptions import (
    ClientAuthenticationError, HttpResponseError, ResourceNotFoundError, ServiceRequestError,
    ServiceResponseError,
)
from azure.storage.blob import BlobServiceClient, ExponentialRetry

from . import exceptions

log = logging.getLogger("ledsync.storage")

CONNECT_TIMEOUT = 8
READ_TIMEOUT = 20
YEAR_CONTAINER_RE = re.compile(r"^\d{4}$")
MAX_FOLDER_CANDIDATES = 50


class StorageError(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass(frozen=True)
class EventLocation:
    container: str
    folder: str            # the event folder name, without slashes: '1000 - Star contender Doha'

    @property
    def guid_blob_path(self) -> str:
        return f"{self.folder}/_GUID.json"

    def blob_url(self, account: str, path: str) -> str:
        """Address of a blob, for the record. Never contains a credential."""
        return f"https://{account}.blob.core.windows.net/{quote(self.container)}/{quote(self.folder)}/{quote(path)}"


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
            retry_policy=ExponentialRetry(initial_backoff=1, increment_base=2, retry_total=2),
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
        years = tuple(n for n in names if YEAR_CONTAINER_RE.match(n))
        found = None if not preferred_container else preferred_container in names
        return ConnectionReport(self._account, names, years, found)

    # --- finding an event ----------------------------------------------------------------
    def _folders_starting_with(self, container: str, prefix: str) -> list[str]:
        client = self._service.get_container_client(container)
        folders = []
        for item in client.walk_blobs(name_starts_with=prefix, delimiter="/"):
            name = getattr(item, "name", "")
            if name.endswith("/"):                                # a 'folder' (BlobPrefix)
                folders.append(name[:-1])
            if len(folders) > MAX_FOLDER_CANDIDATES:
                break
        return folders

    def _year_containers_newest_first(self, exclude: str) -> list[str]:
        names = [c.name if hasattr(c, "name") else str(c) for c in self._service.list_containers()]
        return sorted((n for n in names if YEAR_CONTAINER_RE.match(n) and n != exclude), reverse=True)

    def _containers_to_search(self, preferred_container: str):
        """The preferred container first, then the other year containers (listed lazily, so
        the extra call is only made when the preferred container does not have the event)."""
        if preferred_container:
            yield preferred_container
        yield from self._year_containers_newest_first(preferred_container)

    def find_event(self, event_id: str, preferred_container: str = "") -> EventLocation:
        """Find the folder '<EventID> - <name>' - in the preferred container first, then the
        other year containers, newest first."""
        prefix = f"{event_id} - "
        try:
            for container in self._containers_to_search(preferred_container):
                found = self._search(container, prefix, event_id)
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

    def _search(self, container: str, prefix: str, event_id: str) -> EventLocation | None:
        try:
            folders = self._folders_starting_with(container, prefix)
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

    # --- downloading -----------------------------------------------------------------------------
    def read_blob(self, container: str, path: str, max_bytes: int) -> bytes:
        """Download one blob, refusing to hold more than `max_bytes` (+1 to detect overflow)."""
        try:
            client = self._service.get_blob_client(container=container, blob=path)
            data = client.download_blob(offset=0, length=max_bytes + 1).readall()
        except ResourceNotFoundError:
            raise StorageError(
                exceptions.MISSING_FOLDER,
                "The event folder exists but has no _GUID.json. Run Export Event for this event in the "
                "web application, then try again.") from None
        except Exception as exc:                                  # noqa: BLE001
            raise map_error(exc, "downloading the event configuration") from None
        return bytes(data)
