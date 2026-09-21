"""Event registration and GUID validation (BRD Sections 8, 9, 10).

The event configuration (`_GUID.json`) comes from the web application's Export Event
function. In Phase 3 it is supplied as a local test file; from Phase 4 it is downloaded
from Azure Storage. EITHER WAY IT IS UNTRUSTED INPUT (it may be wrong, stale, hand-edited
or hostile), so it is parsed strictly here and only the validated `EventConfig` is used.

GUID rules (BRD 9.2):
  * First registration: there is no recorded GUID yet, so "validation" means the file's
    `exportGuid` is a well-formed GUID, its `eventId` equals the Event ID entered, and the
    GUID does not already belong to a different event.
  * Event already registered: the recorded GUID is compared with the file's GUID.
    Match -> permitted (nothing to change). Mismatch -> rejected and logged.
  * Re-registration (owner decision 20/09/26): after a mismatch the operator must paste the
    new GUID copied from the web application; it must equal the GUID in the newly supplied
    file before the registration is replaced.
"""

import json
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timezone
from urllib.parse import urlsplit

from . import exceptions, oplog
from .events import parse_timestamp

log = logging.getLogger("ledsync.registration")

MAX_CONFIG_BYTES = 60_000
# Technical safety cap so a hostile file cannot make us build a huge structure. This is NOT
# the business "maximum tables per event" rule, which is still an open BRD Section 36 item.
MAX_TABLES = 100
MAX_TABLE_NUMBER = 9999
MAX_EVENT_ID_LENGTH = 50
MAX_NAME_LENGTH = 200
MAX_URL_LENGTH = 2048

_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,%d}$" % (MAX_EVENT_ID_LENGTH - 1))
_GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_NIL_GUID = "00000000-0000-0000-0000-000000000000"

STATUS_REGISTERED = "Registered"

# Characters that must never reach the screen or the logs from an untrusted file: control
# characters (incl. C1 and DEL), lone surrogates, line/paragraph separators, and the bidi
# embedding/override/isolate characters that can make text display differently from how it
# is stored (spoofing). Zero-width joiners and left/right marks are deliberately ALLOWED:
# right-to-left and Indic scripts (e.g. Arabic or Persian event names) legitimately need them.
_BIDI_SPOOFING = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def _unsafe_char(c: str) -> bool:
    return unicodedata.category(c) in ("Cc", "Cs", "Zl", "Zp") or c in _BIDI_SPOOFING


class RegistrationError(Exception):
    """A rejected registration. `message` is safe to show the operator. When `log` is
    true the rejection is written to the exception log (BRD 9.2 step 6 / 21.1)."""

    def __init__(self, category: str, message: str, *, log: bool = True):
        super().__init__(message)
        self.category = category
        self.message = message
        self.log = log


class InputError(RegistrationError):
    """The operator's own form input was unusable (a typo). Shown, but not an exception."""

    def __init__(self, message: str):
        super().__init__(exceptions.CONFIGURATION, message, log=False)


class GuidMismatch(RegistrationError):
    def __init__(self, event_id: str, recorded: str | None, incoming: str):
        super().__init__(
            exceptions.GUID_VALIDATION,
            f"The GUID in the file does not match the GUID recorded when event {event_id} "
            f"was registered (recorded {recorded or 'none'}, file {incoming}).",
        )
        self.event_id = event_id
        self.recorded = recorded
        self.incoming = incoming


@dataclass(frozen=True)
class TableConfig:
    number: int
    inner: bool
    outer: bool
    main: bool


@dataclass(frozen=True)
class EventConfig:
    event_id: str
    event_name: str
    storage_url: str
    tables: tuple[TableConfig, ...]
    guid: str                    # normalised: lower-case, hyphenated
    export_timestamp: str | None  # only kept when it parses as a real date
    raw_text: str


# --- GUID helpers ------------------------------------------------------------

def normalise_guid(value) -> str | None:
    """Canonical lower-case hyphenated GUID, or None if `value` is not one.
    Tolerates surrounding whitespace and {braces} as pasted from other tools."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1].strip()
    if not _GUID_RE.match(text):
        return None
    text = text.lower()
    return None if text == _NIL_GUID else text


def validate_guid(recorded: str | None, incoming: str | None) -> bool:
    """BRD 9.2: permit only when the two GUIDs match. Anything missing or malformed
    on either side fails closed."""
    a, b = normalise_guid(recorded), normalise_guid(incoming)
    return a is not None and b is not None and a == b


# --- input validation ---------------------------------------------------------

def validate_event_id_input(text: str) -> str:
    value = (text or "").strip()
    if not value:
        raise InputError("Enter the Event ID.")
    if not _EVENT_ID_RE.match(value):
        raise InputError(
            f"The Event ID may use letters, numbers, dot, dash and underscore only "
            f"(up to {MAX_EVENT_ID_LENGTH} characters)."
        )
    return value


def _no_constant(name):
    raise ValueError(f"unsupported constant {name}")


def _no_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate key")
        seen[key] = value
    return seen


def _invalid(message: str) -> RegistrationError:
    return RegistrationError(exceptions.INVALID_CONFIGURATION, message)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _string(obj: dict, key: str, max_len: int) -> str:
    value = obj.get(key)
    if key not in obj or value is None:
        raise _invalid(f"The configuration is missing the required field '{key}'.")
    if _is_int(value):          # some tools write numeric ids as numbers
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"The field '{key}' must be non-empty text.")
    value = value.strip()
    if len(value) > max_len:
        raise _invalid(f"The field '{key}' is too long.")
    if any(_unsafe_char(c) for c in value):
        raise _invalid(f"The field '{key}' contains characters that are not allowed.")
    return value


def _normalise_timestamp(value) -> str | None:
    """UTC ISO-8601 ('2026-09-18T07:02:26.380Z'), or None if absent/unusable/out of range.
    Normalised at the door so later comparisons never depend on the file's spelling."""
    parsed = parse_timestamp(value) if isinstance(value, str) else None
    if parsed is None:
        return None
    utc = parsed.astimezone(timezone.utc)
    text = utc.strftime("%Y-%m-%dT%H:%M:%S")
    if utc.microsecond:
        text += ".%03d" % (utc.microsecond // 1000)
    return text + "Z"


def parse_event_config(data: bytes) -> EventConfig:
    """Strictly parse and validate an event configuration file (BRD Section 10)."""
    if len(data) > MAX_CONFIG_BYTES:
        raise _invalid("The file is too large to be an event configuration.")
    if not data.strip():
        raise _invalid("The file is empty.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _invalid("The file is not valid UTF-8 text.") from None
    try:
        obj = json.loads(text, parse_constant=_no_constant, object_pairs_hook=_no_duplicate_keys)
    except (ValueError, RecursionError):
        raise _invalid("The file is not valid JSON.") from None
    if not isinstance(obj, dict):
        raise _invalid("The configuration must be a JSON object.")

    event_id = _string(obj, "eventId", MAX_EVENT_ID_LENGTH)
    if not _EVENT_ID_RE.match(event_id):
        raise _invalid("The field 'eventId' is not a valid Event ID.")
    event_name = _string(obj, "eventName", MAX_NAME_LENGTH)

    storage_url = _string(obj, "eventStorageUrl", MAX_URL_LENGTH)
    try:
        parts = urlsplit(storage_url)
        host_ok = bool(parts.hostname) and parts.scheme == "https" and parts.username is None \
            and parts.password is None
    except ValueError:
        host_ok = False
    if not host_ok:
        raise _invalid("The field 'eventStorageUrl' must be an https address.")

    guid = normalise_guid(obj.get("exportGuid"))
    if guid is None:
        raise _invalid("The field 'exportGuid' is missing or is not a valid GUID.")

    raw_tables = obj.get("tables")
    if not isinstance(raw_tables, list) or not raw_tables:
        raise _invalid("The configuration must list at least one table.")
    if len(raw_tables) > MAX_TABLES:
        raise _invalid(f"The configuration lists more than {MAX_TABLES} tables.")
    tables, seen = [], set()
    for entry in raw_tables:
        if not isinstance(entry, dict):
            raise _invalid("Each table entry must be an object.")
        number = entry.get("tableNumber")
        if not _is_int(number) or not (1 <= number <= MAX_TABLE_NUMBER):
            raise _invalid("Each table needs a whole 'tableNumber' of 1 or more.")
        if number in seen:
            raise _invalid(f"Table {number} is listed more than once.")
        seen.add(number)
        flags = []
        for key in ("innerLed", "outerLed", "mainLed"):
            flag = entry.get(key)
            if not isinstance(flag, bool):
                raise _invalid(f"Table {number}: '{key}' must be true or false.")
            flags.append(flag)
        tables.append(TableConfig(number, *flags))

    export_timestamp = _normalise_timestamp(obj.get("exportTimestamp"))

    return EventConfig(
        event_id=event_id, event_name=event_name, storage_url=storage_url,
        tables=tuple(tables), guid=guid, export_timestamp=export_timestamp, raw_text=text,
    )


# --- registration ---------------------------------------------------------------

def _ensure_identity(entered_event_id: str, config: EventConfig) -> None:
    # Event IDs are case-insensitive (Windows folders are): 'abc' and 'ABC' are the same event.
    if config.event_id.casefold() != entered_event_id.casefold():
        raise RegistrationError(
            exceptions.CONFIGURATION,
            f"The file is for event {config.event_id}, but you entered event {entered_event_id}. "
            "Check that you chose the right file.",
        )


def _ensure_guid_not_used_elsewhere(conn: sqlite3.Connection, event_id: str, guid: str) -> None:
    for row in conn.execute("SELECT event_id, event_guid FROM events WHERE event_id <> ?", (event_id,)):
        if normalise_guid(row["event_guid"]) == guid:
            raise RegistrationError(
                exceptions.GUID_VALIDATION,
                f"This GUID already belongs to a different registered event ({row['event_id']}). "
                "Assets from one event must never be applied to another.",
            )


def register_event(conn: sqlite3.Connection, entered_event_id: str, config: EventConfig,
                   source: str) -> str:
    """Register a new event. Returns 'registered', or 'already_registered' when the event
    exists with the SAME GUID. Raises GuidMismatch for a different GUID."""
    _ensure_identity(entered_event_id, config)
    row = conn.execute("SELECT event_id, event_guid FROM events WHERE event_id = ? COLLATE NOCASE",
                       (entered_event_id,)).fetchone()
    if row is not None:
        if validate_guid(row["event_guid"], config.guid):
            return "already_registered"
        # Carry the spelling already on record, so re-registration updates that very row.
        raise GuidMismatch(row["event_id"], normalise_guid(row["event_guid"]), config.guid)

    _ensure_guid_not_used_elsewhere(conn, entered_event_id, config.guid)
    try:
        # The event row and its audit row are ONE transaction: both are saved or neither is.
        conn.execute(
            "INSERT INTO events (event_id, event_name, event_guid, configuration_file, "
            "configuration_json, configuration_version, last_updated, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (config.event_id, config.event_name, config.guid, source, config.raw_text,
             config.export_timestamp, config.export_timestamp, STATUS_REGISTERED),
        )
        oplog.add(conn, "Event Registered", "Success",
                  f"Event {config.event_id} registered ({len(config.tables)} table(s)); "
                  f"GUID {config.guid}; source: {source}.", event_id=config.event_id)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise RegistrationError(
            exceptions.CONFIGURATION, f"Event {config.event_id} was registered a moment ago. "
            "Reload the dashboard.") from None
    except sqlite3.Error:
        conn.rollback()
        log.exception("Could not save the registration of event %s", config.event_id)
        raise RegistrationError(exceptions.CONFIGURATION,
                                "The event could not be saved. Try again.", log=False) from None
    return "registered"


def reregister_event(conn: sqlite3.Connection, entered_event_id: str, config: EventConfig,
                     pasted_guid: str, source: str) -> None:
    """Replace an existing registration after the operator confirmed the new GUID."""
    _ensure_identity(entered_event_id, config)
    confirmed = normalise_guid(pasted_guid)
    if confirmed is None:
        raise InputError("That is not a valid GUID. Copy it exactly as shown in the web application.")
    if not validate_guid(confirmed, config.guid):
        raise RegistrationError(
            exceptions.GUID_VALIDATION,
            "The GUID you entered does not match the GUID in the file, so the event was not "
            "re-registered.",
        )
    row = conn.execute("SELECT event_guid FROM events WHERE event_id = ?", (entered_event_id,)).fetchone()
    if row is None:
        raise RegistrationError(exceptions.CONFIGURATION,
                                f"Event {entered_event_id} is not registered, so it cannot be re-registered.")
    old_guid = normalise_guid(row["event_guid"]) or "none"
    _ensure_guid_not_used_elsewhere(conn, entered_event_id, config.guid)
    try:
        # A new export replaces the registration, so nothing has been downloaded or synced
        # from it yet: last_download / last_sync are cleared with the status reset. (Device
        # mappings are left alone for now - revisited at Step 5.2.)
        conn.execute(
            "UPDATE events SET event_name = ?, event_guid = ?, configuration_file = ?, "
            "configuration_json = ?, configuration_version = ?, last_updated = ?, status = ?, "
            "last_download = NULL, last_sync = NULL WHERE event_id = ?",
            (config.event_name, config.guid, source, config.raw_text, config.export_timestamp,
             config.export_timestamp, STATUS_REGISTERED, entered_event_id),
        )
        oplog.add(conn, "Event Re-registered", "Success",
                  f"Event {entered_event_id} re-registered: GUID {old_guid} -> {config.guid}; "
                  f"source: {source}.", event_id=entered_event_id)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        log.exception("Could not save the re-registration of event %s", entered_event_id)
        raise RegistrationError(exceptions.CONFIGURATION,
                                "The event could not be saved. Try again.", log=False) from None


def log_rejection(conn: sqlite3.Connection, err: RegistrationError, operation: str,
                  event_id: str | None, source: str | None) -> None:
    """Write a rejected registration to the exception log (BRD 9.2 step 6)."""
    if err.log:
        exceptions.record(conn, err.category, operation, err.message,
                          event_id=event_id, source=source)


# --- pending re-registration (server-side, short-lived) ------------------------------

@dataclass(frozen=True)
class Pending:
    event_id: str
    config: EventConfig
    source: str
    recorded_guid: str | None
    created: float


class PendingReregistrations:
    """Holds the file that was just rejected for a GUID mismatch so the operator can
    confirm the new GUID on the next screen without re-uploading it. In memory only,
    expires quickly, bounded in size, and referenced from the browser only by an
    unguessable token."""

    def __init__(self, ttl_seconds: float = 900.0, max_items: int = 8,
                 clock: Callable[[], float] = time.monotonic):
        self._ttl, self._max, self._clock = ttl_seconds, max_items, clock
        self._items: dict[str, Pending] = {}
        self._lock = threading.Lock()

    def _purge(self) -> None:
        now = self._clock()
        for token in [t for t, p in self._items.items() if now - p.created > self._ttl]:
            del self._items[token]

    def add(self, event_id: str, config: EventConfig, source: str, recorded_guid: str | None) -> str:
        with self._lock:
            self._purge()
            while len(self._items) >= self._max:          # drop the oldest
                del self._items[min(self._items, key=lambda t: self._items[t].created)]
            token = secrets.token_urlsafe(24)
            self._items[token] = Pending(event_id, config, source, recorded_guid, self._clock())
            return token

    def get(self, token: str | None) -> Pending | None:
        if not token:
            return None
        with self._lock:
            self._purge()
            return self._items.get(token)

    def discard(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._items.pop(token, None)
