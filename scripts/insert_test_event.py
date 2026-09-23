"""DEV/TEST ONLY: insert sample rows into the `events` table so the dashboard can be
seen populated (Implementation Sequence Step 2.1: "manually insert one test row").

Safety guards (this writes FAKE registrations):
  * LEDSYNC_DATA_DIR must be set AND must not be the application's real data folder
    (%LOCALAPPDATA%\\LEDAssetSync) - the script refuses either way.
  * The folder must already exist (a typo must not silently create a new one),
    unless you pass --create.
  * Rows use plain INSERT: an existing event with the same ID is NEVER overwritten
    (it is skipped and reported).
  * Inserted rows are marked configuration_file = 'DEV-TEST-ROW' and --clear removes
    only those.

    $env:LEDSYNC_DATA_DIR = "$env:TEMP\\ledsync-test"
    .\\.venv\\Scripts\\python scripts\\insert_test_event.py --create   # one row
    .\\.venv\\Scripts\\python scripts\\insert_test_event.py --several  # five rows
    .\\.venv\\Scripts\\python scripts\\insert_test_event.py --clear    # remove test rows
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ledsync import config  # noqa: E402
from ledsync.db import connect, init_db  # noqa: E402

MARK = "DEV-TEST-ROW"

# (event_id, name, last_updated (UTC ISO), status). The first mirrors the real test event
# exported on 18 Sep 2026 (its status is blank, as a freshly registered event would be).
ONE = [("1000", "Star contender Doha", "2026-09-18T07:02:26Z", "")]
SEVERAL = ONE + [
    ("1001", "Europe Smash Warm-Up", "2026-09-19T14:30:00Z", "Ready"),
    ("1002", "WTT Feeder Series Final", "2026-09-20T09:05:12Z", "Synced"),
    ("1003", "Youth Contender Muscat", "2026-09-17T21:45:00Z", "Attention needed"),
    ("1004", "Event With No Update Yet", None, None),
]


def real_data_dir() -> Path:
    """The folder the real application uses when LEDSYNC_DATA_DIR is NOT set."""
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "LEDAssetSync") if base else (Path.home() / ".ledassetsync")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--several", action="store_true", help="insert five rows covering every status")
    parser.add_argument("--clear", action="store_true", help="delete rows previously inserted by this script")
    parser.add_argument("--create", action="store_true", help="create the scratch folder if it does not exist")
    args = parser.parse_args()

    raw = os.environ.get("LEDSYNC_DATA_DIR")
    if not raw:
        print("Refusing to run: set LEDSYNC_DATA_DIR to a scratch folder first (see --help).", file=sys.stderr)
        return 2
    target = Path(raw).resolve()
    if target == real_data_dir().resolve():
        print(f"Refusing to run: {target} is the application's REAL data folder. "
              "Use a scratch folder (e.g. $env:TEMP\\ledsync-test).", file=sys.stderr)
        return 2
    if not target.is_dir() and not args.create:
        print(f"Refusing to run: {target} does not exist. Check the path, or add --create.", file=sys.stderr)
        return 2

    cfg = config.load()
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    try:
        if args.clear:
            n = conn.execute("DELETE FROM events WHERE configuration_file = ?", (MARK,)).rowcount
            conn.commit()
            print(f"Removed {n} test row(s) from {cfg.db_path}")
            return 0
        inserted, skipped = 0, []
        for event_id, name, updated, status in (SEVERAL if args.several else ONE):
            try:
                # Distinct per row (events.event_guid is now uniquely indexed, case-insensitively -
                # Phase 13 pre-hand-off review) - a shared placeholder would make every row after
                # the first silently collide and be skipped.
                fake_guid = f"00000000-0000-0000-0000-{event_id.zfill(12)}"
                conn.execute(
                    "INSERT INTO events (event_id, event_name, event_guid, configuration_file, "
                    "last_updated, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (event_id, name, fake_guid, MARK, updated, status),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                skipped.append(event_id)
        conn.commit()
        print(f"Inserted {inserted} test row(s) into {cfg.db_path}")
        if skipped:
            print(f"Skipped existing event(s) with the same ID (left untouched): {', '.join(skipped)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
