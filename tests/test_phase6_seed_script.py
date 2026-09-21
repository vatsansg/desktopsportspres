"""The dev-only script that fakes a little download history (used in the manual Phase 6 test)."""

import os
import subprocess
import sys
from pathlib import Path

from ledsync.db import connect, init_db

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "insert_test_history.py"


def run(data_dir, *args):
    env = dict(os.environ, LEDSYNC_DATA_DIR=str(data_dir)) if data_dir else {k: v for k, v in os.environ.items() if k != "LEDSYNC_DATA_DIR"}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=ROOT)


def history(cfg):
    conn = connect(cfg.db_path)
    try:
        return [tuple(r) for r in conn.execute("SELECT event_id, file_name, local_path, status FROM download_history")]
    finally:
        conn.close()


def register_event(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    conn.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('1000', 'x', 'g')")
    conn.commit()
    conn.close()


def test_it_refuses_without_a_scratch_folder():
    out = run(None)
    assert out.returncode == 2 and "LEDSYNC_DATA_DIR" in out.stderr


def test_it_refuses_the_real_data_folder(monkeypatch):
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return
    out = run(Path(base) / "LEDAssetSync")
    assert out.returncode == 2 and "REAL data folder" in out.stderr


def test_it_refuses_a_folder_that_does_not_exist(tmp_path):
    out = run(tmp_path / "typo")
    assert out.returncode == 2 and "does not exist" in out.stderr


def test_it_refuses_when_the_event_is_not_registered(cfg):
    out = run(cfg.data_dir)
    assert out.returncode == 2 and "not registered" in out.stderr
    assert not cfg.db_path.exists() or history(cfg) == []


def test_it_adds_three_marked_rows_once_and_clears_only_those(cfg):
    register_event(cfg)
    assert run(cfg.data_dir).returncode == 0
    assert run(cfg.data_dir).returncode == 0                       # running twice does not duplicate
    rows = history(cfg)
    assert len(rows) == 3 and all(r[2] == "DEV-TEST-ROW" and r[3] == "Success" for r in rows)
    conn = connect(cfg.db_path)
    conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, local_path, status) "
                 "VALUES ('1000', 'real.png', 1, 'Inner', 'C:/real/real.png', 'Success')")
    conn.commit()
    conn.close()
    assert run(cfg.data_dir, "--clear").returncode == 0
    assert history(cfg) == [("1000", "real.png", "C:/real/real.png", "Success")]
