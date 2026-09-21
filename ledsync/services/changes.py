"""Comparing the cloud change log with what has already been processed (BRD Section 18, Step 6.3).

Rules (owner decisions 21/09/26 and the web BRD v2.4 note):
  * A file is identified by its FULL path - table + LED type + file name - matched ignoring case
    (Windows semantics; the real cloud folders are lower-case while the log says `Inner`). An
    update to `Table 1/Inner/sponsorsequence.csv` never masks one to `Table 1/Outer/...`.
  * Only the LATEST cloud entry for a path matters. It is compared with the latest local record
    (a successful download or a deletion) for the same path, comparing UTC instants parsed from
    the change log's own timestamps - never this computer's clock.
  * New / Updated in the cloud, and nothing (or something older) here -> DOWNLOAD.
  * Deleted in the cloud, and a successful local copy older than that -> DELETE LOCAL COPY
    (owner decision; the deletion itself is carried out in Phase 7 - nothing here touches a file).
  * Everything else that is up to date -> ALREADY PROCESSED.
  * Entries that are not `Table N/<LED type>/<file>` for a table and LED type the event actually
    enables are listed as NOT APPLICABLE, with the reason, and are never acted on.
  * The "Last Updated Timestamp Cut-off" (BRD Section 14) is still an open BRD item; the owner
    chose UTC comparison with no cut-off for now (a setting comes with Phase 10).

This module only reads and decides: it changes no file and no database row.
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from . import exceptions, oplog
from . import registration as reg
from . import structure
from .changelog import ChangeLogError, CloudEntry, ParsedChangeLog, SkippedRow, fetch_change_log
from .events import parse_timestamp
from .storage import StorageError

DOWNLOAD = "Download"
DELETE = "Delete local copy"
DONE = "Already processed"
NOT_APPLICABLE = "Not applicable"

LABEL_NEW, LABEL_UPDATED, LABEL_REMOVED = "New", "Updated", "Removed in cloud"

_TABLE_RE = re.compile(r"^Table (\d{1,4})$", re.IGNORECASE)
LED_ORDER = {t: i for i, t in enumerate(structure.LED_TYPES)}


def path_key(table: int, led_type: str, file_name: str) -> str:
    return f"{int(table)}/{led_type.casefold()}/{file_name.casefold()}"


@dataclass(frozen=True)
class LocalRecord:
    status: str                       # Success | Deleted
    source_time: datetime


@dataclass(frozen=True)
class Assessment:
    path: str                         # as written in the cloud change log
    table: int | None
    led_type: str | None              # canonical: Inner | Outer | MainLED
    file_name: str
    cloud_status: str                 # New | Updated | Deleted
    cloud_time_text: str
    cloud_time: datetime
    action: str                       # DOWNLOAD | DELETE | DONE | NOT_APPLICABLE
    label: str                        # New | Updated | Removed in cloud | Already processed | Not applicable
    reason: str
    revisions: int                    # how many log entries there are for this path


@dataclass(frozen=True)
class Comparison:
    assessments: tuple[Assessment, ...]
    skipped: tuple[SkippedRow, ...]
    total_entries: int
    source_name: str

    def with_action(self, action: str) -> list[Assessment]:
        return [a for a in self.assessments if a.action == action]

    def count(self, action: str) -> int:
        return sum(1 for a in self.assessments if a.action == action)

    def count_label(self, label: str) -> int:
        return sum(1 for a in self.assessments if a.label == label)

    @property
    def distinct_paths(self) -> int:
        return len(self.assessments)


def local_state(conn: sqlite3.Connection, event_id: str) -> dict[str, LocalRecord]:
    """The latest successful download or deletion per path (failed downloads do not count)."""
    latest: dict[str, LocalRecord] = {}
    rows = conn.execute("SELECT table_number, led_type, file_name, source_timestamp, status FROM download_history "
                        "WHERE event_id = ? COLLATE NOCASE AND status IN ('Success', 'Deleted') "
                        "ORDER BY download_id", (event_id,)).fetchall()
    for r in rows:
        led = structure.canonical_led_type(r["led_type"])
        when = parse_timestamp(r["source_timestamp"])
        if led is None or r["table_number"] is None or not r["file_name"] or when is None:
            continue
        key = path_key(r["table_number"], led, r["file_name"])
        current = latest.get(key)
        if current is None or when >= current.source_time:
            latest[key] = LocalRecord(r["status"], when)
    return latest


def _classify_path(path: str, enabled: set) -> tuple[int, str, str] | str:
    """(table, canonical LED type, file name) for a usable path, or the reason it is not applicable."""
    parts = path.split("/")
    if len(parts) < 3:
        return "The file is not inside a Table / LED-type folder."
    if len(parts) > 3:
        return "Files inside sub-folders are not supported."
    match = _TABLE_RE.match(parts[0])
    if not match:
        return "The file is not inside a Table folder."
    led = structure.canonical_led_type(parts[1])
    if led is None:
        return "The folder is not a known LED type (Inner, Outer or Main LED)."
    table = int(match.group(1))
    if (table, led) not in enabled:
        return f"Table {table} {structure.LED_LABELS[led]} is not part of this event."
    return table, led, parts[2]


def compare(parsed: ParsedChangeLog, event_structure: structure.EventStructure,
            local: dict[str, LocalRecord]) -> Comparison:
    enabled = set(event_structure.enabled_pairs)
    groups: dict[str, list[CloudEntry]] = {}
    for entry in parsed.entries:
        groups.setdefault(entry.path.casefold(), []).append(entry)

    out: list[Assessment] = []
    for entries in groups.values():
        latest = max(entries, key=lambda e: (e.timestamp, e.sno if e.sno is not None else -1, e.line))
        where = _classify_path(latest.path, enabled)
        if isinstance(where, str):
            out.append(Assessment(latest.path, None, None, latest.path.rsplit("/", 1)[-1], latest.status,
                                  latest.timestamp_text, latest.timestamp, NOT_APPLICABLE, NOT_APPLICABLE, where,
                                  len(entries)))
            continue
        table, led, name = where
        have = local.get(path_key(table, led, name))
        action, label, reason = _decide(latest, have)
        out.append(Assessment(latest.path, table, led, name, latest.status, latest.timestamp_text, latest.timestamp,
                              action, label, reason, len(entries)))
    out.sort(key=lambda a: (a.action == NOT_APPLICABLE, a.table or 0, LED_ORDER.get(a.led_type or "", 99),
                            a.path.casefold()))
    return Comparison(tuple(out), parsed.skipped, len(parsed.entries), parsed.source_name)


def _decide(latest: CloudEntry, have: LocalRecord | None) -> tuple[str, str, str]:
    if latest.status == "Deleted":
        if have is not None and have.status == "Success" and have.source_time < latest.timestamp:
            return DELETE, LABEL_REMOVED, "Removed in the cloud after it was downloaded; the local copy will be deleted."
        if have is None:
            return DONE, DONE, "Removed in the cloud; nothing is stored locally."
        return DONE, DONE, "The removal has already been processed."
    # New or Updated
    if have is None:
        return DOWNLOAD, LABEL_NEW, "Not downloaded yet."
    if have.source_time < latest.timestamp:
        if have.status == "Deleted":
            return DOWNLOAD, LABEL_NEW, "Added again in the cloud after it was removed."
        return DOWNLOAD, LABEL_UPDATED, "Changed in the cloud after it was last downloaded."
    return DONE, DONE, "Already downloaded or removed at or after this change."


# --- the whole check ------------------------------------------------------------------------------------

class CheckError(Exception):
    """A check that could not be completed. `message` is plain text that is safe to show."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass(frozen=True)
class Report:
    event_id: str
    comparison: Comparison
    checked_at: datetime
    folder: str


def check_event(conn: sqlite3.Connection, storage, settings, event_id: str) -> Report:
    """Fetch the event's change log and compare it. Read-only against Azure; writes only an audit row
    (and, on failure, an exception row). Raises CheckError with a safe message."""
    operation = "Check Changes"
    try:
        row = conn.execute("SELECT event_id, event_guid FROM events WHERE event_id = ? COLLATE NOCASE",
                           (event_id,)).fetchone()
        if row is None:
            raise CheckError(exceptions.CONFIGURATION, "That event is not registered.")
        event_id = row["event_id"]
        try:
            event_structure = structure.load_structure(conn, event_id)
        except structure.StructureError as err:
            raise CheckError(exceptions.INVALID_CONFIGURATION, str(err)) from None
        registered = reg.normalise_guid(row["event_guid"] or "")
        if not registered:
            raise CheckError(exceptions.GUID_VALIDATION,
                             "This event has no recorded GUID. Re-register it before checking for changes.")

        from . import cloud                                   # local import: cloud pulls in the Azure layer
        fetched = cloud.fetch_event_config(storage, settings, event_id)
        if reg.normalise_guid(fetched.config.guid) != registered:
            raise CheckError(exceptions.GUID_VALIDATION,
                             "The web application has exported this event again (its GUID changed). "
                             "Re-register the event, then check for changes.")
        parsed = fetch_change_log(storage, fetched.location)
        comparison = compare(parsed, event_structure, local_state(conn, event_id))
    except CheckError as err:
        _log_failure(conn, event_id, operation, err.category, err.message)
        raise
    except (StorageError, ChangeLogError, reg.RegistrationError) as err:
        _log_failure(conn, event_id, operation, err.category, err.message)
        raise CheckError(err.category, err.message) from None

    oplog.record(conn, operation, "Success",
                 f"{comparison.count(DOWNLOAD)} to download, {comparison.count(DELETE)} to remove, "
                 f"{comparison.count(DONE)} already processed, {comparison.count(NOT_APPLICABLE)} not applicable, "
                 f"{len(comparison.skipped)} unreadable row(s) in {comparison.source_name}.", event_id)
    return Report(event_id, comparison, datetime.now(timezone.utc), fetched.location.folder)


def _log_failure(conn, event_id, operation, category, message) -> None:
    exceptions.record(conn, category, operation, message, event_id=event_id)
    oplog.record(conn, operation, "Failed", message, event_id)
