"""Reading the web application's change log, `_ledassetschangelog.csv` (BRD Section 16, Step 6.1).

The change log is UNTRUSTED input from cloud storage: it is size-capped, strictly decoded and every
row is validated. A row that cannot be trusted is SKIPPED and REPORTED (never silently dropped and
never allowed to stop the rest); a file that cannot be understood at all is refused with a plain
message. Nothing here downloads an asset or touches a local file - it only reads and parses.

Real-event facts this module is built around (checked against the live test event 1000):
  * the real file is `_ledassetschangelog.csv` (with an 's'); the BRD spelling
    `_ledassetchangelog.csv` is tried second;
  * columns are `sno, filename, changetimestamp, status, username`;
  * `filename` is a full relative path `Table 1/Inner/FFTT.png` (web BRD v2.4): the SAME file name
    exists once per table per LED type, so the path - never the bare name - identifies a file;
  * timestamps are UTC ISO-8601 (`2026-09-17T06:28:15.668Z`); a value with no zone is read as UTC;
  * statuses are New, Updated and Deleted.
"""

import csv
import io
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from . import exceptions
from .events import parse_timestamp
from .storage import _UNSAFE_PATH, StorageError

NAMES = ("_ledassetschangelog.csv", "_ledassetchangelog.csv")   # real name first, BRD spelling second
MAX_BYTES = 5_000_000
MAX_ROWS = 50_000
MAX_PATH_LENGTH = 1024
STATUSES = {"new": "New", "updated": "Updated", "deleted": "Deleted"}
REQUIRED = ("filename", "changetimestamp", "status")


class ChangeLogError(Exception):
    """The whole file is unusable. `message` is plain text that is safe to show."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass(frozen=True)
class CloudEntry:
    line: int                    # line number in the file, for the record
    sno: int | None
    path: str                    # e.g. 'Table 1/Inner/FFTT.png', exactly as in the file
    timestamp_text: str          # as in the file (kept verbatim for comparison records)
    timestamp: datetime          # aware
    status: str                  # New | Updated | Deleted


@dataclass(frozen=True)
class SkippedRow:
    line: int
    reason: str                  # fixed plain text, never the row's own content


@dataclass(frozen=True)
class ParsedChangeLog:
    entries: tuple[CloudEntry, ...]
    skipped: tuple[SkippedRow, ...]
    source_name: str


def _path_problem(path: str) -> str | None:
    """Why a path from the file cannot be trusted, or None. Fixed text only."""
    if not path:
        return "The file name is empty."
    if len(path) > MAX_PATH_LENGTH:
        return "The file path is too long."
    if _UNSAFE_PATH.search(path) or any(
            unicodedata.category(ch)[0] == "C" or (unicodedata.category(ch)[0] == "Z" and ch != " ") for ch in path):
        return "The file path contains characters that are not allowed."
    if path.startswith("/") or any(p in ("", ".", "..") for p in path.split("/")):
        return "The file path is not a valid relative path."
    return None


def parse_change_log(data: bytes, source_name: str = NAMES[0]) -> ParsedChangeLog:
    if len(data) > MAX_BYTES:
        raise ChangeLogError(exceptions.CONFIGURATION, "The change log is too large to read (over 5 MB).")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ChangeLogError(exceptions.CONFIGURATION, "The change log is not valid text (UTF-8).") from None
    if "\x00" in text:
        raise ChangeLogError(exceptions.CONFIGURATION, "The change log is not a readable CSV file.")

    reader = csv.reader(io.StringIO(text, newline=""))
    entries: list[CloudEntry] = []
    skipped: list[SkippedRow] = []
    index: dict[str, int] | None = None
    try:
        for row in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in row):
                continue                                         # blank line
            if index is None:
                names = [cell.strip().casefold() for cell in row]
                missing = [c for c in REQUIRED if c not in names]
                if missing:
                    raise ChangeLogError(exceptions.CONFIGURATION,
                                         "The change log does not have the expected columns "
                                         "(filename, changetimestamp, status).")
                index = {name: i for i, name in enumerate(names)}
                continue
            if len(entries) + len(skipped) >= MAX_ROWS:
                raise ChangeLogError(exceptions.CONFIGURATION,
                                     f"The change log has more than {MAX_ROWS} rows, which is more than this "
                                     "application reads.")
            entry, reason = _row(row, line, index)
            if entry is not None:
                entries.append(entry)
            else:
                skipped.append(SkippedRow(line, reason))
    except csv.Error:
        raise ChangeLogError(exceptions.CONFIGURATION, "The change log is not a readable CSV file.") from None
    if index is None:
        raise ChangeLogError(exceptions.CONFIGURATION, "The change log is empty.")
    return ParsedChangeLog(tuple(entries), tuple(skipped), source_name)


def _row(row: list[str], line: int, index: dict[str, int]) -> tuple[CloudEntry | None, str]:
    if len(row) <= max(index[c] for c in REQUIRED):
        return None, "The row is missing columns."
    path = row[index["filename"]].strip()
    problem = _path_problem(path)
    if problem:
        return None, problem
    status = STATUSES.get(row[index["status"]].strip().casefold())
    if status is None:
        return None, "The status is not New, Updated or Deleted."
    stamp_text = row[index["changetimestamp"]].strip()
    stamp = parse_timestamp(stamp_text)
    if stamp is None:
        return None, "The change time cannot be read."
    sno = None
    if "sno" in index and index["sno"] < len(row):
        try:
            sno = int(row[index["sno"]].strip())
        except ValueError:
            sno = None
    return CloudEntry(line, sno, path, stamp_text, stamp, status), ""


def fetch_change_log(storage, location) -> ParsedChangeLog:
    """Download and parse the event's change log (read-only). `location` is the VERIFIED event
    location. Raises StorageError (Azure problems) or ChangeLogError (the file is unusable)."""
    for name in NAMES:
        try:
            data = storage.read_blob(location, name, MAX_BYTES)
        except StorageError as err:
            if err.category == exceptions.MISSING_FOLDER:
                continue                                         # try the other spelling
            raise
        return parse_change_log(data, name)
    raise ChangeLogError(exceptions.MISSING_FOLDER,
                         "This event has no change log yet. Upload assets in the web application, then check again.")
