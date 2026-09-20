"""Step 0.2 - empty SQLite database with the seven BRD Section 26 tables."""

import sqlite3

import pytest

from ledsync.db import SCHEMA_VERSION, TABLES, connect, init_db
from ledsync.db.connection import SchemaError

BRD_SECTION_26_TABLES = {
    "events", "led_mappings", "download_history", "sync_history",
    "application_settings", "operation_log", "exception_log",
}

# Columns exactly as listed in BRD Section 26 (before approved extensions).
BRD_SECTION_26_COLUMNS = {
    "events": ["event_id", "event_name", "event_guid", "configuration_file",
               "configuration_version", "last_updated", "last_download", "last_sync", "status"],
    "led_mappings": ["mapping_id", "event_id", "table_number", "led_type", "ip_address",
                     "shared_folder", "enabled", "last_connection_test", "connection_status"],
    "download_history": ["download_id", "event_id", "file_name", "table_number", "led_type",
                         "source_path", "local_path", "source_timestamp", "download_timestamp", "status"],
    "sync_history": ["sync_id", "event_id", "file_name", "destination", "sync_timestamp",
                     "status", "error_message"],
    "application_settings": ["setting_name", "setting_value"],
    "operation_log": ["log_id", "event_id", "operation", "timestamp", "status", "message"],
    "exception_log": ["exception_id", "event_id", "timestamp", "operation", "category",
                      "message", "resolution_status"],
}

# Approved extensions (20 Sep 2026): Section 7.1 config JSON + Section 21.1 exception fields.
APPROVED_EXTENSIONS = {
    "events": {"configuration_json"},
    "exception_log": {"table_number", "led_type", "file_name", "source", "destination"},
}


def _raw_tables(path):
    # Deliberately bypasses ledsync.db so the check is independent of app code.
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        conn.close()


def _raw_columns(path, table):
    conn = sqlite3.connect(path)
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def test_creates_exactly_the_seven_tables(cfg):
    init_db(cfg.db_path)
    assert _raw_tables(cfg.db_path) == BRD_SECTION_26_TABLES


def test_every_brd_column_is_present_in_order(cfg):
    init_db(cfg.db_path)
    for table, brd_cols in BRD_SECTION_26_COLUMNS.items():
        actual = _raw_columns(cfg.db_path, table)
        assert [c for c in actual if c in brd_cols] == brd_cols, table


def test_only_approved_extensions_beyond_brd_columns(cfg):
    init_db(cfg.db_path)
    for table, brd_cols in BRD_SECTION_26_COLUMNS.items():
        extras = set(_raw_columns(cfg.db_path, table)) - set(brd_cols)
        assert extras == APPROVED_EXTENSIONS.get(table, set()), table


def test_tables_registry_matches_real_schema(cfg):
    init_db(cfg.db_path)
    for table, cols in TABLES.items():
        assert _raw_columns(cfg.db_path, table) == cols


def test_all_tables_are_empty(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    try:
        for table in TABLES:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    finally:
        conn.close()


def test_init_is_idempotent_and_preserves_data(cfg):
    """BRD 28.2 / Business Rule 9: re-initialising must never clear existing data."""
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    conn.execute("INSERT INTO events (event_id, event_name) VALUES ('1000', 'Star contender Doha')")
    conn.commit()
    conn.close()

    init_db(cfg.db_path)  # second run, as an upgrade/relaunch would do

    conn = connect(cfg.db_path)
    try:
        rows = conn.execute("SELECT event_id, event_name FROM events").fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in rows] == [("1000", "Star contender Doha")]


def test_schema_version_recorded(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_newer_database_is_refused_not_downgraded(cfg):
    init_db(cfg.db_path)
    conn = sqlite3.connect(cfg.db_path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaError):
        init_db(cfg.db_path)


def test_foreign_keys_enforced_on_mappings(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO led_mappings (event_id, table_number, led_type) "
                         "VALUES ('nope', 1, 'Inner')")
    finally:
        conn.close()


def test_logs_accept_events_that_are_not_registered(cfg):
    """A rejected registration (Step 3.2) must be loggable for an unregistered event."""
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    try:
        conn.execute("INSERT INTO exception_log (event_id, timestamp, category) "
                     "VALUES ('9999', '2026-09-20T00:00:00Z', 'GUID validation')")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM exception_log").fetchone()[0] == 1
    finally:
        conn.close()


def test_init_creates_missing_parent_directory(tmp_path):
    db = tmp_path / "nested" / "deeper" / "ledsync.db"
    init_db(db)
    assert db.exists()
