"""Device and folder mapping (BRD Sections 12, 26) - which shared folder each LED destination uses.

Rules implemented here:
  * Only LED types ENABLED for a table in the event configuration can be mapped (BRD 12,
    Business Rule 5). A form post naming anything else is ignored.
  * A mapping is an optional IP address (recorded, not used to decide anything - folder access
    is what is tested, owner decision 21/09/26) and a Windows shared folder.
  * Two LED destinations of one event may share a device but NOT a folder (files would overwrite
    each other).
  * When an event is re-registered and its structure changes, matching mappings are kept (and
    marked 'Not tested'), mappings no longer in the event are HIDDEN (enabled = 0, never
    deleted), and new LEDs appear unmapped (owner decision 21/09/26).
  * The application stores NO credentials for shared folders (the authentication method for the
    real venue network is still an open BRD Section 36 item): it relies on the Windows access of
    the account it runs under.

`shared_folder` is UNTRUSTED input that later phases will WRITE FILES into, so it is validated
strictly (`validate_shared_folder`).
"""

import ipaddress
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PureWindowsPath

from . import oplog
from .structure import LED_LABELS, LED_TYPES

STATUS_UNMAPPED = "Not mapped"
STATUS_UNTESTED = "Not tested"
STATUS_OK = "Connection Successful"
STATUS_FAILED = "Connection Failed"

MAX_PATH_LENGTH = 240          # comfortably inside the classic Windows 260-character limit
_BAD_CHARS = re.compile(r'[\x00-\x1f<>"|?*]')
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class MappingError(ValueError):
    """A rejected mapping; the message is safe to show and names the destination it is about."""


@dataclass(frozen=True)
class Mapping:
    mapping_id: int
    event_id: str
    table_number: int
    led_type: str
    ip_address: str
    shared_folder: str
    enabled: bool
    last_connection_test: str | None
    connection_status: str | None

    @property
    def label(self) -> str:
        return f"Table {self.table_number} {LED_LABELS.get(self.led_type, self.led_type)}"

    @property
    def status(self) -> str:
        if not self.shared_folder:
            return STATUS_UNMAPPED
        return self.connection_status or STATUS_UNTESTED

    @property
    def key(self) -> str:
        return f"{self.table_number}-{self.led_type}"


# --- validation ------------------------------------------------------------------------------------------

def validate_ip(text: str, label: str = "") -> str:
    value = (text or "").strip()
    if not value:
        return ""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise MappingError(f"{label + ': ' if label else ''}'{value[:45]}' is not a valid IP address "
                           "(for example 192.168.1.20).") from None


def _protected_roots() -> list[PureWindowsPath]:
    """Folders the application must never be pointed at: they hold the operating system or other
    programs, and (below) the application's own data."""
    roots = []
    for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
        value = os.environ.get(name)
        if value:
            roots.append(PureWindowsPath(value))
    return roots


def _is_inside(path: PureWindowsPath, root: PureWindowsPath) -> bool:
    p, r = [s.casefold() for s in path.parts], [s.casefold() for s in root.parts]
    return len(p) >= len(r) and p[: len(r)] == r


def validate_shared_folder(text: str, label: str = "", forbidden_roots: tuple = ()) -> str:
    """Return the normalised folder, or raise MappingError.

    Accepted: `\\\\host\\share[\\sub\\folders]` (UNC, BRD 20's example) or an absolute local path
    `X:\\folder\\...` (for the simulated development folders). Refused: relative paths, `..`,
    device paths (`\\\\?\\`, `\\\\.\\`), wildcard/control characters, reserved device names, trailing
    dots/spaces, over-long paths, drive roots, operating-system folders, and the application's own
    data folder (files pushed there could overwrite the database)."""
    prefix = f"{label}: " if label else ""
    value = (text or "").strip().replace("/", "\\")
    if not value:
        return ""
    if len(value) > MAX_PATH_LENGTH:
        raise MappingError(f"{prefix}the folder path is too long (over {MAX_PATH_LENGTH} characters).")
    if _BAD_CHARS.search(value):
        raise MappingError(f"{prefix}the folder path contains characters that are not allowed.")

    if value.startswith("\\\\"):
        parts = value[2:].split("\\")
        host = parts[0]
        if host in ("?", ".") or not _HOST_RE.match(host):
            raise MappingError(f"{prefix}use a network folder like \\\\device\\share, not a device path.")
        segments = parts[1:]
        if not segments or not segments[0]:
            raise MappingError(f"{prefix}a network folder needs a share name, like \\\\{host}\\share.")
        head = "\\\\" + host
    elif re.match(r"^[A-Za-z]:\\", value):
        head = value[:2]
        segments = value[3:].split("\\")
        if segments == [""]:
            raise MappingError(f"{prefix}choose a folder on the drive, not the whole drive.")
    else:
        raise MappingError(f"{prefix}enter a full folder path such as \\\\device\\share\\folder or C:\\LED\\Inner.")

    if segments and segments[-1] == "":
        segments = segments[:-1]                              # one trailing backslash is fine
    for seg in segments:
        if seg == "" or seg in (".", ".."):
            raise MappingError(f"{prefix}the folder path may not contain empty, '.' or '..' parts.")
        if seg != seg.rstrip(" .") or seg != seg.lstrip(" "):
            raise MappingError(f"{prefix}folder names may not start with a space or end with a space or dot.")
        if ":" in seg:
            raise MappingError(f"{prefix}folder names may not contain a colon.")   # NTFS alternate data stream
        if seg.split(".")[0].upper() in _RESERVED:
            raise MappingError(f"{prefix}'{seg}' is a reserved Windows name.")

    normal = head + "\\" + "\\".join(segments)
    pure = PureWindowsPath(normal)
    for root in [*_protected_roots(), *[PureWindowsPath(str(r)) for r in forbidden_roots]]:
        if _is_inside(pure, root):
            raise MappingError(f"{prefix}that folder is reserved for the operating system or this application. "
                               "Choose a dedicated folder for the LED files.")
    return normal


def folder_key(path: str) -> str:
    """Windows folder names ignore case, so duplicates are detected ignoring case."""
    return path.casefold().rstrip("\\")


# --- persistence --------------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(r: sqlite3.Row) -> Mapping:
    return Mapping(r["mapping_id"], r["event_id"], r["table_number"], r["led_type"], r["ip_address"] or "",
                   r["shared_folder"] or "", bool(r["enabled"]), r["last_connection_test"], r["connection_status"])


def list_mappings(conn: sqlite3.Connection, event_id: str, *, enabled: bool = True) -> list[Mapping]:
    rows = conn.execute("SELECT * FROM led_mappings WHERE event_id = ? AND enabled = ? "
                        "ORDER BY table_number, led_type", (event_id, 1 if enabled else 0)).fetchall()
    order = {t: i for i, t in enumerate(LED_TYPES)}
    return sorted((_row(r) for r in rows), key=lambda m: (m.table_number, order.get(m.led_type, 99)))


def reconcile(conn: sqlite3.Connection, event_id: str, pairs, *, reset_status: bool = False) -> None:
    """Make the mapping rows match the event's enabled LEDs. Does NOT commit - callers run it inside
    their own transaction (registration / re-registration)."""
    wanted = set(pairs)
    existing = {(r["table_number"], r["led_type"]): r
                for r in conn.execute("SELECT * FROM led_mappings WHERE event_id = ?", (event_id,))}
    for pair in wanted:
        row = existing.get(pair)
        if row is None:
            conn.execute("INSERT INTO led_mappings (event_id, table_number, led_type, ip_address, shared_folder, enabled) "
                         "VALUES (?, ?, ?, '', '', 1)", (event_id, pair[0], pair[1]))
        else:
            conn.execute("UPDATE led_mappings SET enabled = 1 WHERE mapping_id = ?", (row["mapping_id"],))
            if reset_status:
                conn.execute("UPDATE led_mappings SET connection_status = NULL, last_connection_test = NULL "
                             "WHERE mapping_id = ?", (row["mapping_id"],))
    for pair, row in existing.items():
        if pair not in wanted:
            conn.execute("UPDATE led_mappings SET enabled = 0 WHERE mapping_id = ?", (row["mapping_id"],))


def ensure_rows(conn: sqlite3.Connection, event_id: str, structure) -> None:
    """Idempotent safety net used when a page is opened (creates any missing rows, restores hidden ones)."""
    reconcile(conn, event_id, structure.enabled_pairs)
    conn.commit()


def save_mappings(conn: sqlite3.Connection, event_id: str, structure, entries: dict,
                  forbidden_roots: tuple = ()) -> list[str]:
    """`entries` maps (table_number, led_type) -> (ip, folder) as typed. Entries for anything that is
    not an enabled LED of this event are IGNORED. Validates everything first; saves all or nothing.
    Returns the labels of the destinations that changed."""
    enabled = set(structure.enabled_pairs)
    current = {(m.table_number, m.led_type): m for m in list_mappings(conn, event_id)}
    cleaned, errors, seen = {}, [], {}
    for pair in sorted(enabled, key=lambda p: (p[0], LED_TYPES.index(p[1]))):
        if pair not in entries:
            continue
        ip_text, folder_text = entries[pair]
        label = f"Table {pair[0]} {LED_LABELS[pair[1]]}"
        try:
            ip = validate_ip(ip_text, label)
            folder = validate_shared_folder(folder_text, label, forbidden_roots)
        except MappingError as err:
            errors.append(str(err))
            continue
        if folder:
            key = folder_key(folder)
            if key in seen:
                errors.append(f"{label}: this folder is already used by {seen[key]}. "
                              "Each LED destination needs its own folder.")
                continue
            seen[key] = label
        cleaned[pair] = (ip, folder)
    if errors:
        raise MappingError(" ".join(errors))

    # A folder used by an enabled destination that was NOT part of this post still counts.
    for pair, m in current.items():
        if pair not in cleaned and m.shared_folder and folder_key(m.shared_folder) in seen:
            raise MappingError(f"Table {pair[0]} {LED_LABELS[pair[1]]} already uses "
                               f"{seen[folder_key(m.shared_folder)]}'s folder.")

    changed = []
    try:
        for pair, (ip, folder) in cleaned.items():
            m = current.get(pair)
            if m is not None and m.ip_address == ip and m.shared_folder == folder:
                continue
            if m is None:
                conn.execute("INSERT INTO led_mappings (event_id, table_number, led_type, ip_address, shared_folder, enabled) "
                             "VALUES (?, ?, ?, ?, ?, 1)", (event_id, pair[0], pair[1], ip, folder))
            else:
                # a changed destination has not been tested yet
                conn.execute("UPDATE led_mappings SET ip_address = ?, shared_folder = ?, connection_status = NULL, "
                             "last_connection_test = NULL WHERE mapping_id = ?", (ip, folder, m.mapping_id))
            changed.append(f"Table {pair[0]} {LED_LABELS[pair[1]]}")
        oplog.add(conn, "Mapping Saved", "Success",
                  (f"Device mapping saved for {', '.join(changed)}." if changed else "Device mapping saved (no change)."),
                  event_id=event_id)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise MappingError("The mapping could not be saved. Try again.") from None
    return changed


def record_test(conn: sqlite3.Connection, mapping: Mapping, ok: bool, message: str, category: str | None,
                warning: str = "") -> None:
    """Store a connection-test outcome on the mapping and in the logs (BRD 13 step 6), in one transaction."""
    from . import exceptions

    status = STATUS_OK if ok else STATUS_FAILED
    try:
        conn.execute("UPDATE led_mappings SET connection_status = ?, last_connection_test = ? WHERE mapping_id = ?",
                     (status, _now(), mapping.mapping_id))
        oplog.add(conn, "Device Test", "Success" if ok else "Failed", f"{mapping.label}: {status}. {message}".strip(),
                  event_id=mapping.event_id)
        if not ok:
            conn.execute(
                "INSERT INTO exception_log (event_id, timestamp, operation, category, message, resolution_status, "
                "table_number, led_type, destination) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (mapping.event_id, _now(), "Test Connection", category or exceptions.NETWORK_DEVICE, message,
                 exceptions.OPEN, mapping.table_number, mapping.led_type, mapping.shared_folder))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise MappingError("The test result could not be saved.") from None
