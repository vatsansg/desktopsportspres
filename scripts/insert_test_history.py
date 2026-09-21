"""DEV/TEST ONLY: pretend a few files of event 1000 were already downloaded, so the Change Log page
(Phase 6) can show "Already processed", "Updated" and "Removed in cloud" against the real change log.

Nothing is downloaded and no file is created: this only inserts rows into `download_history`.

Safety guards (same as insert_test_event.py):
  * LEDSYNC_DATA_DIR must be set AND must not be the application's real data folder.
  * The event must already be registered in that database (register it in the app first).
  * Rows carry local_path = 'DEV-TEST-ROW' and --clear removes only those.

    $env:LEDSYNC_DATA_DIR = "$env:TEMP\\ledsync-test-p6"
    .\\.venv\\Scripts\\python scripts\\insert_test_history.py          # add the three sample history rows
    .\\.venv\\Scripts\\python scripts\\insert_test_history.py --clear  # remove them again

The three rows (event 1000, Table 1):
  Inner  FFTT.png              at its cloud time            -> shows as "Already processed"
  Inner  gamebreak.mp4         downloaded before the cloud deleted it   -> shows as "Removed in cloud"
  Outer  sponsorsequence.csv   downloaded before later updates          -> shows as "Updated"
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
EVENT = "1000"
ROWS = [
    (1, "Inner", "FFTT.png", "2026-09-17T06:28:15.668Z"),
    (1, "Inner", "gamebreak.mp4", "2026-09-17T06:28:17.937Z"),
    (1, "Outer", "sponsorsequence.csv", "2026-09-17T07:00:00.000Z"),
]


def real_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "LEDAssetSync") if base else (Path.home() / ".ledassetsync")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--clear", action="store_true", help="delete rows previously inserted by this script")
    args = parser.parse_args()

    raw = os.environ.get("LEDSYNC_DATA_DIR")
    if not raw:
        print("Refusing to run: set LEDSYNC_DATA_DIR to a scratch folder first (see --help).", file=sys.stderr)
        return 2
    target = Path(raw).resolve()
    if target == real_data_dir().resolve():
        print(f"Refusing to run: {target} is the application's REAL data folder.", file=sys.stderr)
        return 2
    if not target.is_dir():
        print(f"Refusing to run: {target} does not exist. Start the application once with it first.", file=sys.stderr)
        return 2

    cfg = config.load()
    if Path(cfg.data_dir).resolve() != target:
        print(f"Refusing to run: the application would use {cfg.data_dir}, not {target}.", file=sys.stderr)
        return 2
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    try:
        if args.clear:
            n = conn.execute("DELETE FROM download_history WHERE local_path = ?", (MARK,)).rowcount
            conn.commit()
            print(f"Removed {n} test history row(s) from {cfg.db_path}")
            return 0
        if conn.execute("SELECT 1 FROM events WHERE event_id = ?", (EVENT,)).fetchone() is None:
            print(f"Refusing to run: event {EVENT} is not registered in {cfg.db_path}. Register it in the app first.",
                  file=sys.stderr)
            return 2
        added = 0
        for table, led, name, stamp in ROWS:
            exists = conn.execute("SELECT 1 FROM download_history WHERE event_id = ? AND table_number = ? AND led_type = ? "
                                  "AND file_name = ? AND local_path = ?", (EVENT, table, led, name, MARK)).fetchone()
            if exists:
                continue
            conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, source_path, local_path, "
                         "source_timestamp, download_timestamp, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Success')",
                         (EVENT, name, table, led, f"Table {table}/{led}/{name}", MARK, stamp, stamp))
            added += 1
        conn.commit()
        print(f"Inserted {added} test history row(s) into {cfg.db_path}")
        return 0
    except sqlite3.Error as err:
        print(f"Database problem: {err}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
