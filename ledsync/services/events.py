"""Event register queries and display formatting (BRD Section 7).

Everything here is read-only: Phase 2 shows what is in the `events` table.
Registration (writes) arrives in Phase 3.

Timestamps are stored as text. Values written by this application are ISO-8601 UTC
(`2026-09-18T07:02:26Z`), but rows may also be inserted by hand or by other tools,
so parsing is lenient. A value with no time zone is treated as UTC. Display uses
the BRD 7.2 format `DD/MM/YY HH:MM` (24-hour) in the venue machine's LOCAL time
(owner decision 20/09/26). This is display only: it does not decide the open
BRD Section 36 question about cut-off/comparison time zones (Step 6.3).

ROBUSTNESS RULE: nothing in this module may raise because of what is stored in the
database. One odd row must never hide the whole event list. Windows cannot convert
instants before 1970 (or far in the future) to local time, and two-digit years are
ambiguous from 2100, so only 1970-2098 is accepted as a date; anything else is
shown as the raw stored text.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo

log = logging.getLogger("ledsync.events")

EMPTY = "—"  # em dash
MIN_YEAR, MAX_YEAR = 1970, 2098   # 2098 so a zone ahead of UTC can never roll to 2100 (two-digit year 00)

# Owner-chosen event status vocabulary (20/09/26). Later phases set these values:
#   Registered       GUID validated and event stored; nothing downloaded yet.
#   Ready            Configured and mapped; ready for Download & Sync.
#   Synced           The last Download & Sync completed successfully.
#   Attention needed The last run had failures/exceptions, or a check failed.
STATUSES = ("Registered", "Ready", "Synced", "Attention needed")
DEFAULT_STATUS = "Registered"
_STATUS_LOOKUP = {s.casefold(): s for s in STATUSES}
_STATUS_KEYS = {"Registered": "registered", "Ready": "ready", "Synced": "synced",
                "Attention needed": "attention"}


@dataclass(frozen=True)
class EventRow:
    event_id: str
    event_name: str
    last_updated: str      # display text, already formatted (BRD 7.2)
    status: str            # display text
    status_key: str        # css-friendly key: registered / ready / synced / attention / other
    # Raw stored values, kept so later phases (live status, Actions column, sorting,
    # <time datetime>) never have to reverse-engineer display text.
    last_updated_raw: str = ""
    status_raw: str = ""


def _text(value) -> str:
    """Any stored value as clean text (SQLite is loosely typed: BLOBs, ints, NULLs)."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace").strip()
    return str(value).strip()


def parse_timestamp(value) -> datetime | None:
    """Parse a stored timestamp to an aware datetime, or None if empty, unparseable
    or outside 1970-2098. Never raises."""
    text = _text(value)
    if not text or "\x00" in text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)  # accepts 'T' or ' ', fractions, offsets
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        utc = parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    if not (MIN_YEAR <= utc.year <= MAX_YEAR):
        return None
    return parsed


def format_timestamp(value, tz: tzinfo | None = None, seconds: bool = False) -> str:
    """BRD 7.2 display: DD/MM/YY HH:MM, 24-hour (BRD 25 log display: DD/MM/YY HH:MM:SS with `seconds`), in `tz` (default: this machine's
    local time). Empty -> an em dash. Unparseable/out-of-range -> the raw text
    (never hidden). Never raises."""
    raw = _text(value)
    parsed = parse_timestamp(value)
    if parsed is None:
        return raw or EMPTY
    try:
        d = parsed.astimezone(tz)
        # Formatted by hand: strftime's %y is platform-dependent.
        stamp = f"{d.day:02d}/{d.month:02d}/{d.year % 100:02d} {d.hour:02d}:{d.minute:02d}"
        return f"{stamp}:{d.second:02d}" if seconds else stamp
    except (ValueError, OverflowError, OSError):
        return raw or EMPTY


def normalise_status(value) -> tuple[str, str]:
    """Return (display text, css key). Blank -> Registered. Unknown text is shown
    as-is (never dropped) with a neutral style."""
    text = _text(value)
    if not text:
        return DEFAULT_STATUS, "registered"
    known = _STATUS_LOOKUP.get(text.casefold())
    if known is None:
        return text, "other"
    return known, _STATUS_KEYS[known]


def _build_row(r, tz: tzinfo | None) -> EventRow:
    status, key = normalise_status(r["status"])
    return EventRow(
        event_id=_text(r["event_id"]),
        event_name=_text(r["event_name"]),
        last_updated=format_timestamp(r["last_updated"], tz),
        status=status,
        status_key=key,
        last_updated_raw=_text(r["last_updated"]),
        status_raw=_text(r["status"]),
    )


def list_events(conn: sqlite3.Connection, tz: tzinfo | None = None) -> list[EventRow]:
    """All registered events, most recently updated first (events with no usable
    timestamp last, then by Event ID). One row that cannot be processed is skipped
    (and logged) rather than hiding the whole list."""
    # TEXT holding invalid UTF-8 (hand edit / corruption) makes sqlite3 itself raise during
    # fetch - before any per-row protection could help. Decode leniently for this query only.
    previous_factory = conn.text_factory
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        rows = conn.execute(
            "SELECT event_id, event_name, last_updated, status FROM events"
        ).fetchall()
    finally:
        conn.text_factory = previous_factory

    def sort_key(row):
        try:
            ts = parse_timestamp(row["last_updated"])
            return (ts is None, -ts.timestamp() if ts else 0.0, _text(row["event_id"]))
        except Exception:  # defensive: sorting must never fail the page
            return (True, 0.0, _text(row["event_id"]))

    result = []
    for r in sorted(rows, key=sort_key):
        try:
            result.append(_build_row(r, tz))
        except Exception:
            log.exception("Skipping an events row that could not be displayed (event_id=%r)",
                          _text(r["event_id"]))
    return result
