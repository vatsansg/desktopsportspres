"""Shared helpers for the Phase 6 tests (change log parsing, comparison, the Changes page)."""

import json
from pathlib import Path

from ledsync.services import structure
from ledsync.services.changelog import parse_change_log

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "reference-docs" / "samplefiles-webassetmgmt"
SAMPLE_LOG = SAMPLE_DIR / "_ledassetschangelog.csv"
REAL_GUID_JSON = json.loads((SAMPLE_DIR / "_GUID.json").read_text(encoding="utf-8"))

T0 = "2026-09-17T06:00:00.000Z"
T1 = "2026-09-17T07:00:00.000Z"
T2 = "2026-09-17T08:00:00.000Z"
T3 = "2026-09-17T09:00:00.000Z"


def csv_text(rows, header="sno,filename,changetimestamp,status,username") -> str:
    """rows: (filename, timestamp, status) tuples -> a change log text with sequential sno."""
    lines = [header]
    for i, (name, stamp, status) in enumerate(rows, 1):
        lines.append(f'{i},"{name}",{stamp},{status},tester')
    return "\n".join(lines) + "\n"


def parsed(rows, **kw):
    return parse_change_log(csv_text(rows, **kw).encode("utf-8"))


def two_table_structure() -> structure.EventStructure:
    """Tables 1 and 2, each with Inner and Outer (the Step 6.1 validation case)."""
    return structure.EventStructure((structure.TableStructure(1, True, True, False),
                                     structure.TableStructure(2, True, True, False)))


def real_structure() -> structure.EventStructure:
    """Event 1000: Table 1 Inner/Outer/MainLED, Table 2 Inner."""
    return structure.EventStructure((structure.TableStructure(1, True, True, True),
                                     structure.TableStructure(2, True, False, False)))


def add_history(conn, event_id, table, led, name, source_ts, status="Success"):
    conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, source_path, local_path, "
                 "source_timestamp, download_timestamp, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (event_id, name, table, led, f"src/{table}/{led}/{name}", f"local/{table}/{led}/{name}",
                  source_ts, "2026-09-20T10:00:00Z", status))
    conn.commit()
