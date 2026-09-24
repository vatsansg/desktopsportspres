"""Phase 13 - Installer and Upgrade Handling: the schema migration runner (db/migrations.py) and
pre-install configuration seeding (services/install_config.py)."""

import sqlite3
import sys
from pathlib import Path

import pytest

from ledsync.db import connect, init_db
from ledsync.db.migrations import Migration, run_migrations
from ledsync.services import install_config as ic
from ledsync.services import scheduler
from ledsync.services import settings as cs

# --- migration runner (generic mechanics, independent of any real app migration) --------------------

def test_run_migrations_applies_every_step_strictly_between_current_and_target_in_order(tmp_path):
    db = tmp_path / "x.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (n INTEGER)")
    conn.commit()
    applied = []
    migrations = (
        Migration(2, "add a", lambda c: (applied.append(2), c.execute("ALTER TABLE t ADD COLUMN a TEXT"))),
        Migration(3, "add b", lambda c: (applied.append(3), c.execute("ALTER TABLE t ADD COLUMN b TEXT"))),
        Migration(4, "add c", lambda c: (applied.append(4), c.execute("ALTER TABLE t ADD COLUMN c TEXT"))),
    )
    import ledsync.db.migrations as mod
    orig = mod.MIGRATIONS
    mod.MIGRATIONS = migrations
    try:
        run_migrations(conn, current_version=1, target_version=3)     # only 2 and 3, never 4
    finally:
        mod.MIGRATIONS = orig
    assert applied == [2, 3]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    columns = {r[1] for r in conn.execute("PRAGMA table_info(t)")}
    assert columns == {"n", "a", "b"}
    conn.close()


def test_run_migrations_is_a_no_op_when_already_at_target(tmp_path):
    db = tmp_path / "x.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 2")
    calls = []
    migrations = (Migration(2, "x", lambda c: calls.append(1)),)
    import ledsync.db.migrations as mod
    orig = mod.MIGRATIONS
    mod.MIGRATIONS = migrations
    try:
        run_migrations(conn, current_version=2, target_version=2)
    finally:
        mod.MIGRATIONS = orig
    assert calls == []
    conn.close()


def test_a_real_schema_upgrade_is_recorded_in_the_operational_log(cfg):
    """Found by the Phase 13 pre-hand-off review: a real schema upgrade previously left no
    operational-log trace at all (BRD Section 25's "Application Update" operation was reserved
    in oplog.OPERATIONS but nothing ever wrote it)."""
    conn = sqlite3.connect(cfg.db_path)
    conn.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY, event_name TEXT NOT NULL, event_guid TEXT, "
                 "configuration_file TEXT, configuration_json TEXT, configuration_version TEXT, "
                 "last_updated TEXT, last_download TEXT, last_sync TEXT, status TEXT)")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    init_db(cfg.db_path)                                              # the "upgrade"
    conn = connect(cfg.db_path)
    rows = conn.execute("SELECT operation, status, message FROM operation_log "
                        "WHERE operation = 'Application Update'").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "Success"
    assert "version 1 to 2" in rows[0]["message"]
    conn.close()


def test_a_fresh_database_never_gets_an_application_update_row(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    rows = conn.execute("SELECT * FROM operation_log WHERE operation = 'Application Update'").fetchall()
    assert rows == []
    conn.close()


def test_init_db_on_a_real_pre_phase_10_database_reaches_current_schema_version(cfg):
    """The one real migration registered so far (cutoff_enabled/cutoff_time, Phase 10) - proves the
    framework against an actual upgrade, not just synthetic migrations."""
    from ledsync.db import SCHEMA_VERSION, TABLES
    conn = sqlite3.connect(cfg.db_path)
    conn.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY, event_name TEXT NOT NULL, event_guid TEXT, "
                 "configuration_file TEXT, configuration_json TEXT, configuration_version TEXT, "
                 "last_updated TEXT, last_download TEXT, last_sync TEXT, status TEXT)")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert [r["name"] for r in conn.execute("PRAGMA table_info(events)")] == TABLES["events"]
    conn.close()


# --- install_config: load_config_file ----------------------------------------------------------------

def test_load_config_file_parses_valid_json(tmp_path):
    path = tmp_path / "install-config.json"
    path.write_text('{"cloud_storage_account": "sasportspresentation"}', encoding="utf-8")
    assert ic.load_config_file(path) == {"cloud_storage_account": "sasportspresentation"}


def test_load_config_file_refuses_invalid_json(tmp_path):
    path = tmp_path / "install-config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ic.InstallConfigError):
        ic.load_config_file(path)


def test_load_config_file_refuses_a_non_object_json_document(tmp_path):
    path = tmp_path / "install-config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ic.InstallConfigError):
        ic.load_config_file(path)


def test_load_config_file_refuses_a_missing_file(tmp_path):
    with pytest.raises(ic.InstallConfigError):
        ic.load_config_file(tmp_path / "does-not-exist.json")


def test_load_config_file_refuses_a_schedule_password_field(tmp_path):
    path = tmp_path / "install-config.json"
    path.write_text('{"schedule_password": "hunter2"}', encoding="utf-8")
    with pytest.raises(ic.InstallConfigError) as exc:
        ic.load_config_file(path)
    assert "hunter2" not in str(exc.value)                             # never echoed back


def test_load_config_file_refuses_a_schedule_enabled_field(tmp_path):
    path = tmp_path / "install-config.json"
    path.write_text('{"schedule_enabled": true}', encoding="utf-8")
    with pytest.raises(ic.InstallConfigError):
        ic.load_config_file(path)


def test_load_config_file_refuses_a_quoted_string_for_email_enabled(tmp_path):
    """Found by the Phase 13 pre-hand-off review: Python's bool("false") is True, so a hand-edit
    mistake like this would otherwise silently turn email notifications ON."""
    path = tmp_path / "install-config.json"
    path.write_text('{"email_enabled": "false"}', encoding="utf-8")
    with pytest.raises(ic.InstallConfigError):
        ic.load_config_file(path)


def test_load_config_file_accepts_a_real_json_boolean_for_email_enabled(tmp_path):
    path = tmp_path / "install-config.json"
    path.write_text('{"email_enabled": false}', encoding="utf-8")
    data = ic.load_config_file(path)
    assert data["email_enabled"] is False


def test_load_config_file_refuses_an_unrecognised_field(tmp_path):
    path = tmp_path / "install-config.json"
    path.write_text('{"totally_made_up_field": "x"}', encoding="utf-8")
    with pytest.raises(ic.InstallConfigError):
        ic.load_config_file(path)


def test_load_config_file_ignores_underscore_prefixed_documentation_keys(tmp_path):
    path = tmp_path / "install-config.json"
    path.write_text('{"_comment": "edit me", "cloud_storage_account": "sasportspresentation"}', encoding="utf-8")
    data = ic.load_config_file(path)
    assert data["cloud_storage_account"] == "sasportspresentation"


def test_the_example_install_config_file_stays_in_sync_with_the_real_schema():
    """The example file ships in installer/ for operators to copy and edit - if the field schema
    ever changes, this fails loudly instead of the example silently drifting out of date."""
    example = Path(__file__).resolve().parent.parent / "installer" / "install-config.example.json"
    ic.load_config_file(example)                                     # must not raise


# --- install_config: should_seed / seed --------------------------------------------------------------

def test_should_seed_is_true_only_for_a_database_with_no_saved_settings(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    assert ic.should_seed(conn)
    cs.save_download_settings(conn, "3", "5")
    assert not ic.should_seed(conn)
    conn.close()


def test_seed_applies_cloud_email_and_schedule_defaults_through_the_real_settings_functions(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    data = {
        "cloud_storage_account": "sasportspresentation",
        "cloud_container": "2026",
        "storage_access_key": "A" * 88,
        "email_enabled": True,
        "email_recipient": "it@example.com",
        "email_sender": "noreply@example.com",
        "email_connection_string": "endpoint=https://x.communication.azure.com/;accesskey=" + "A" * 40,
        "schedule_days": ["Mon", "Wed"],
        "schedule_time": "21:00",
        "schedule_username": "vatsanwindows",
        "download_retry_count": 4,
        "download_retry_delay": 10,
        "log_retention_days": 90,
    }
    changed = ic.seed(conn, data, cfg.data_dir)
    assert changed == ["cloud storage", "email notification",
                       "scheduling defaults (not enabled - the password is never read from this file)",
                       "download retry", "log retention"]
    cloud = cs.load_cloud(conn)
    assert cloud.account == "sasportspresentation" and cloud.container == "2026" and cloud.has_key
    email = cs.load_email(conn)
    assert email.enabled and email.recipient == "it@example.com" and email.sender == "noreply@example.com"
    schedule = cs.load_schedule(conn)
    assert schedule.enabled is False                                   # never turned on by the config file
    assert schedule.days == ("Mon", "Wed") and schedule.time == "21:00" and schedule.username == "vatsanwindows"
    conn.close()


def test_seed_refuses_to_run_against_a_database_that_already_has_settings(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    cs.save_download_settings(conn, "3", "5")                          # already "configured"
    with pytest.raises(ic.InstallConfigError):
        ic.seed(conn, {"cloud_storage_account": "sasportspresentation"}, cfg.data_dir)
    assert cs.load_cloud(conn).account == ""                           # nothing from the file got in
    conn.close()


def test_seed_propagates_the_same_validation_settings_pages_already_enforce(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    with pytest.raises(ic.InstallConfigError):
        ic.seed(conn, {"cloud_storage_account": "NOT-VALID-!!!"}, cfg.data_dir)
    assert cs.load_cloud(conn).account == ""                           # rejected, nothing partially saved
    conn.close()


def test_seed_with_an_empty_config_object_changes_nothing_and_is_not_an_error(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    assert ic.seed(conn, {}, cfg.data_dir) == []
    conn.close()


# --- scheduler: frozen vs. source command/arguments --------------------------------------------------

def test_a_source_run_registers_python_dash_m_scheduled_run(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    command, arguments = scheduler._command_and_args(tmp_path)
    assert command == sys.executable
    assert arguments == f'-m ledsync.scheduled_run --data-dir "{tmp_path}"'


def test_a_frozen_build_registers_the_dedicated_scheduled_run_exe_next_to_the_main_one(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\LEDAssetSync\LEDAssetSync.exe", raising=False)
    command, arguments = scheduler._command_and_args(tmp_path)
    assert command == str(Path(r"C:\Program Files\LEDAssetSync") / scheduler.SCHEDULED_RUN_EXE_NAME)
    assert arguments == f'--data-dir "{tmp_path}"'
    assert "-m ledsync.scheduled_run" not in arguments


def test_task_xml_uses_the_frozen_scheduled_run_exe_when_sys_frozen_is_set(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\LEDAssetSync\LEDAssetSync.exe", raising=False)
    xml = scheduler._task_xml("user", ("Mon",), "09:30", tmp_path, tmp_path)
    assert "LEDAssetSyncScheduled.exe" in xml
    assert "ledsync.scheduled_run" not in xml


# --- main.py: --seed-config integration --------------------------------------------------------------

def test_run_seed_config_applies_the_file_to_a_brand_new_database(cfg):
    from ledsync import main as main_mod
    init_db(cfg.db_path)
    path = cfg.data_dir / "install-config.json"
    path.write_text('{"cloud_storage_account": "sasportspresentation", "cloud_container": "2026", '
                    '"storage_access_key": "' + "A" * 88 + '"}', encoding="utf-8")
    main_mod._run_seed_config(cfg, path)
    conn = connect(cfg.db_path)
    assert cs.load_cloud(conn).account == "sasportspresentation"
    conn.close()


def test_run_seed_config_is_silently_a_no_op_on_an_already_configured_database(cfg):
    from ledsync import main as main_mod
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    cs.save_download_settings(conn, "3", "5")
    conn.close()
    path = cfg.data_dir / "install-config.json"
    path.write_text('{"cloud_storage_account": "sasportspresentation"}', encoding="utf-8")
    main_mod._run_seed_config(cfg, path)                              # must not raise
    conn = connect(cfg.db_path)
    assert cs.load_cloud(conn).account == ""                          # never touched
    conn.close()


def test_run_seed_config_with_a_bad_file_never_raises(cfg):
    from ledsync import main as main_mod
    init_db(cfg.db_path)
    path = cfg.data_dir / "install-config.json"
    path.write_text("{not json", encoding="utf-8")
    main_mod._run_seed_config(cfg, path)                              # logged, not fatal


def test_seed_mode_never_checks_for_webview2(cfg, monkeypatch):
    """--seed-config must work in a build/CI environment with no WebView2 runtime at all - it never
    opens a window."""
    import argparse

    from ledsync import main as main_mod
    from ledsync import platform_checks

    def _boom():
        raise AssertionError("webview2_version() must not be called in --seed-config mode")
    monkeypatch.setattr(platform_checks, "webview2_version", _boom)
    monkeypatch.setattr(main_mod.config, "load", lambda: cfg)
    path = cfg.data_dir / "install-config.json"
    path.write_text("{}", encoding="utf-8")
    main_mod._run(argparse.Namespace(auto_close=None, seed_config=path))
