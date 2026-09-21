"""Fetching an event's configuration from Azure (Step 4.2) and trusting it only after checks.

Flow: find the event folder from the Event ID -> download `_GUID.json` (read-only, size
capped) -> strictly parse/validate it exactly as in Phase 3 -> verify the storage address
inside the file points at the same account, container and folder it was downloaded from
(finding S-14: an untrusted file must not steer later downloads, or a credential, to
another location) -> hand the validated `EventConfig` to registration.

Later phases must NEVER build a download address from `config.storage_url`; they use the
verified `EventLocation` (container + folder) and re-verify per download.
"""

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from . import exceptions
from . import registration as reg
from .settings import CloudSettings
from .storage import EventLocation

_MISMATCH_HOST = ("The event configuration points to a different storage account than the one configured, "
                  "so it was not trusted.")
_MISMATCH_PATH = ("The storage address inside the event configuration does not match the folder it was "
                  "downloaded from, so it was not trusted.")


@dataclass(frozen=True)
class FetchedEvent:
    config: reg.EventConfig
    location: EventLocation
    source: str            # the blob address for the record - never contains a credential


def _reject(message: str) -> reg.RegistrationError:
    return reg.RegistrationError(exceptions.CONFIGURATION, message)


def verify_storage_location(config: reg.EventConfig, account: str, location: EventLocation) -> None:
    """The `eventStorageUrl` in the file must be exactly
    https://<account>.blob.core.windows.net/<container>/<folder> - the very place the file was
    downloaded from. No query string, fragment or parameters (they could carry a credential),
    no port other than 443, and the path is compared SEGMENT BY SEGMENT before decoding so an
    encoded slash (%2F) cannot smuggle a different path past the comparison."""
    try:
        parts = urlsplit(config.storage_url)
        port = parts.port                                   # raises ValueError for a malformed port
    except ValueError:
        raise _reject(_MISMATCH_HOST) from None
    if (parts.scheme != "https" or (parts.hostname or "").lower() != f"{account}.blob.core.windows.net"
            or port not in (None, 443) or parts.username is not None or parts.password is not None):
        raise _reject(_MISMATCH_HOST)
    if parts.query or parts.fragment or ";" in parts.path:
        raise _reject("The storage address inside the event configuration carries extra parts (a query or "
                      "fragment), so it was not trusted.")
    segments = parts.path.split("/")
    if segments and segments[0] == "":
        segments = segments[1:]
    if segments and segments[-1] == "":                     # one trailing slash is fine
        segments = segments[:-1]
    if len(segments) != 2 or any(s == "" for s in segments):
        raise _reject(_MISMATCH_PATH)
    decoded = [unquote(s) for s in segments]
    if any("/" in d or "\\" in d for d in decoded) or decoded != [location.container, location.folder]:
        raise _reject(_MISMATCH_PATH)


def fetch_event_config(storage, settings: CloudSettings, event_id: str) -> FetchedEvent:
    """Raises `StorageError` (Azure problems) or `RegistrationError` (the file is unacceptable)."""
    location = storage.find_event(event_id, settings.container)
    data = storage.read_blob(location, "_GUID.json", reg.MAX_CONFIG_BYTES)
    config = reg.parse_event_config(data)
    verify_storage_location(config, settings.account, location)
    return FetchedEvent(config, location, location.blob_url(settings.account, "_GUID.json"))
