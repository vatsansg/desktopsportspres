"""Fetching an event's configuration from Azure (Step 4.2) and trusting it only after checks.

Flow: find the event folder from the Event ID -> download `_GUID.json` (read-only, size
capped) -> strictly parse/validate it exactly as in Phase 3 -> verify the storage address
inside the file points at the same account, container and folder it was downloaded from
(finding S-14: an untrusted file must not steer later downloads, or a credential, to
another location) -> hand the validated `EventConfig` to registration.
"""

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from . import exceptions
from . import registration as reg
from .settings import CloudSettings
from .storage import EventLocation


@dataclass(frozen=True)
class FetchedEvent:
    config: reg.EventConfig
    location: EventLocation
    source: str            # the blob address for the record - never contains a credential


def verify_storage_location(config: reg.EventConfig, account: str, location: EventLocation) -> None:
    """The `eventStorageUrl` in the file must be https://<account>.blob.core.windows.net/<container>/<folder>
    - the very place the file was downloaded from."""
    parts = urlsplit(config.storage_url)
    expected_host = f"{account}.blob.core.windows.net"
    if (parts.hostname or "").lower() != expected_host or parts.port not in (None, 443):
        raise reg.RegistrationError(
            exceptions.CONFIGURATION,
            "The event configuration points to a different storage account than the one configured, "
            "so it was not trusted.")
    path = unquote(parts.path).strip("/")
    if path != f"{location.container}/{location.folder}":
        raise reg.RegistrationError(
            exceptions.CONFIGURATION,
            "The storage address inside the event configuration does not match the folder it was "
            "downloaded from, so it was not trusted.")


def fetch_event_config(storage, settings: CloudSettings, event_id: str) -> FetchedEvent:
    """Raises `StorageError` (Azure problems) or `RegistrationError` (the file is unacceptable)."""
    location = storage.find_event(event_id, settings.container)
    data = storage.read_blob(location.container, location.guid_blob_path, reg.MAX_CONFIG_BYTES)
    config = reg.parse_event_config(data)
    verify_storage_location(config, settings.account, location)
    return FetchedEvent(config, location, location.blob_url(settings.account, "_GUID.json"))
