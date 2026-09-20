"""Startup, configuration, logging and failure-path behaviour (ledsync.main)."""

import subprocess
import sys
from pathlib import Path

import pytest

from ledsync import config, main as main_mod, platform_checks
from ledsync.db import init_db
from ledsync.db.connection import SchemaError, connect, _verify
from ledsync.db.schema import SCHEMA_VERSION


@pytest.fixture
def dialogs(monkeypatch):
    """Capture fatal dialogs instead of showing a real message box."""
    shown = []
    monkeypatch.setattr(platform_checks, "fatal_dialog", lambda title, msg: shown.append((title, msg)))
    return shown


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDSYNC_DATA_DIR", str(tmp_path / "appdata"))
    return tmp_path / "appdata"


# --- config ---------------------------------------------------------------

def test_data_dir_override_via_env(data_dir):
    cfg = config.load()
    assert cfg.data_dir == data_dir
    assert cfg.db_path == data_dir / "ledsync.db"
    assert data_dir.is_dir()


def test_data_dir_defaults_to_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("LEDSYNC_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert config.default_data_dir() == tmp_path / "LEDAssetSync"


def test_data_dir_falls_back_to_home_when_no_localappdata(monkeypatch):
    monkeypatch.delenv("LEDSYNC_DATA_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert config.default_data_dir() == Path.home() / ".ledassetsync"


# --- WebView2 detection ---------------------------------------------------

def test_webview2_detected_on_this_machine():
    if sys.platform != "win32":
        pytest.skip("Windows only")
    assert platform_checks.webview2_version() is not None


def test_webview2_absent_reports_none(monkeypatch):
    monkeypatch.setattr(platform_checks, "_read_pv", lambda hive, subkey: None)
    assert platform_checks.webview2_version() is None


def test_webview2_version_returned_when_present(monkeypatch):
    monkeypatch.setattr(platform_checks, "_read_pv", lambda hive, subkey: "153.0.1")
    assert platform_checks.webview2_version() == "153.0.1"


# --- failure paths --------------------------------------------------------

def test_missing_webview2_shows_plain_language_dialog_and_exits_1(data_dir, dialogs, monkeypatch):
    monkeypatch.setattr(platform_checks, "webview2_version", lambda: None)
    assert main_mod.main([]) == 1
    assert len(dialogs) == 1
    assert "WebView2" in dialogs[0][1]
    assert not (data_dir / "ledsync.db").exists()  # failed before touching the DB


def test_newer_database_shows_dialog_logs_to_file_and_exits_1(data_dir, dialogs, monkeypatch):
    monkeypatch.setattr(platform_checks, "webview2_version", lambda: "1.0")
    data_dir.mkdir(parents=True)
    init_db(data_dir / "ledsync.db")
    conn = connect(data_dir / "ledsync.db")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    assert main_mod.main([]) == 1
    assert len(dialogs) == 1 and "newer version" in dialogs[0][1]
    log = (data_dir / "logs" / "ledsync.log").read_text(encoding="utf-8")
    assert "Application failed" in log and "SchemaError" in log


def test_unwritable_data_dir_still_fails_gracefully(tmp_path, dialogs, monkeypatch):
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    monkeypatch.setenv("LEDSYNC_DATA_DIR", str(blocker / "sub"))  # parent is a file
    assert main_mod.main([]) == 1
    assert len(dialogs) == 1


def test_schema_drift_is_detected(data_dir):
    data_dir.mkdir(parents=True)
    db = data_dir / "ledsync.db"
    init_db(db)
    conn = connect(db)
    conn.execute("ALTER TABLE events ADD COLUMN surprise TEXT")
    conn.commit()
    with pytest.raises(SchemaError):
        _verify(conn)
    conn.close()


def test_file_logging_created_and_console_optional(data_dir):
    from ledsync import logging_setup

    path = logging_setup.setup_logging(data_dir)
    assert path == data_dir / "logs" / "ledsync.log"
    assert path.exists()


# --- import hygiene -------------------------------------------------------

def test_importing_main_does_not_load_the_ui_toolkit():
    """A future headless/scheduled entry point must not drag in pythonnet/WinForms."""
    code = "import sys, ledsync.main; print('webview' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_running_package_module_is_guarded():
    """`import ledsync.__main__` must not start the app."""
    code = "import ledsync.__main__; print('imported-only')"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "imported-only" in out.stdout


# --- SQLite settings ------------------------------------------------------

def test_connection_uses_wal_busy_timeout_and_foreign_keys(data_dir):
    data_dir.mkdir(parents=True)
    conn = connect(data_dir / "ledsync.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
