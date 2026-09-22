"""Phase 10 - remaining Application Settings (Download, Scheduling, Email, Application/retention), the per-event
timestamp cut-off, and the fuller Dashboard Actions column (Steps 10.1, 10.2)."""

import re
import threading
import time
from datetime import datetime

import pytest
from conftest import csrf_from, db_rows, do_login
from phase6_helpers import real_structure

from ledsync.db import connect, init_db
from ledsync.services import changes, exceptions, mappings, oplog, settings as cs, sync, transfer
from test_phase8_sync import ev, local_file, map_devices, no_retry_pause, run, seed, sync_all, text_of, tok, world  # noqa: F401


def tok2(client, path):
    return csrf_from(client, path)


# --- services/settings.py: Download, Scheduling, Email, Retention --------------------------------------------------------------

def test_download_settings_default_to_the_transfer_module_constants(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    saved = cs.load_download_settings(conn)
    assert (saved.retry_count, saved.retry_delay) == (transfer.RETRY_COUNT, transfer.RETRY_DELAY)
    assert saved.retry_count_is_default and saved.retry_delay_is_default
    conn.close()


def test_download_settings_save_validate_and_blank_means_default(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    assert cs.save_download_settings(conn, "3", "7.5") == ["retry count", "retry delay"]
    saved = cs.load_download_settings(conn)
    assert (saved.retry_count, saved.retry_delay) == (3, 7.5)
    assert not saved.retry_count_is_default and not saved.retry_delay_is_default
    assert cs.save_download_settings(conn, "", "") == ["retry count", "retry delay"]
    assert cs.load_download_settings(conn).retry_count_is_default
    for count, delay in (("-1", "5"), ("6", "5"), ("abc", "5"), ("2", "-1"), ("2", "61"), ("2", "abc")):
        with pytest.raises(cs.SettingsError):
            cs.save_download_settings(conn, count, delay)
    assert db_rows(cfg, "SELECT COUNT(*) AS n FROM operation_log WHERE operation = 'Settings Changed'")[0]["n"] == 2
    conn.close()


def test_log_retention_default_is_forever(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    assert cs.load_log_retention(conn) == cs.RetentionSettings("", None)
    assert cs.save_log_retention(conn, "30") is True
    assert cs.load_log_retention(conn) == cs.RetentionSettings("30", 30)
    assert cs.save_log_retention(conn, "") is True
    assert cs.load_log_retention(conn).days is None
    for bad in ("0", "3651", "abc", "-5"):
        with pytest.raises(cs.SettingsError):
            cs.save_log_retention(conn, bad)
    conn.close()


def test_scheduling_settings_validate_order_and_refuse_enabling_without_day_or_time(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    assert cs.load_schedule(conn) == cs.ScheduleSettings(False, (), "")
    with pytest.raises(cs.SettingsError):
        cs.save_schedule(conn, True, ["Mon"], "")
    with pytest.raises(cs.SettingsError):
        cs.save_schedule(conn, True, [], "02:30")
    changed = cs.save_schedule(conn, True, ["Fri", "Mon", "Mon", "Xyz"], "02:30")     # forged/duplicate days dropped
    assert changed == ["enabled", "days", "time"]
    saved = cs.load_schedule(conn)
    assert saved == cs.ScheduleSettings(True, ("Mon", "Fri"), "02:30")               # week order, not input order
    with pytest.raises(cs.SettingsError):
        cs.save_schedule(conn, True, ["Mon"], "25:00")
    with pytest.raises(cs.SettingsError):
        cs.save_schedule(conn, True, ["Mon"], "9:30")
    assert cs.save_schedule(conn, True, ["Mon", "Fri"], "02:30") == []               # no change
    conn.close()


def test_email_settings_validate_recipient_and_never_echo_the_connection_string(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    assert cs.load_email(conn) == cs.EmailSettings(False, "", False)
    with pytest.raises(cs.SettingsError):
        cs.save_email(conn, True, "", "")                        # enabling needs a recipient
    changed = cs.save_email(conn, True, "it@example.com", "endpoint=https://x.communication.azure.com/;accesskey=" + "A" * 40)
    assert changed == ["enabled", "recipient", "connection string"]
    saved = cs.load_email(conn)
    assert saved.enabled and saved.recipient == "it@example.com" and saved.has_connection
    assert cs.save_email(conn, True, "it@example.com", "") == []                     # blank keeps the existing connection
    assert cs.load_email(conn).has_connection
    for bad_recipient in ("not-an-email", "a" * 40 + "@" + "b" * 40 + "@x.com"):
        with pytest.raises(cs.SettingsError):
            cs.save_email(conn, False, bad_recipient, "")
    with pytest.raises(cs.SettingsError):
        cs.save_email(conn, False, "", "short")                  # too short to be a real connection string
    conn.close()


def test_a_key_shaped_value_pasted_into_the_recipient_box_is_refused(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    with pytest.raises(cs.SettingsError):
        cs.save_email(conn, False, "sk_live_" + "A1b2C3d4" * 6, "")
    conn.close()


def test_owned_keys_cover_every_new_setting():
    for key in (cs.KEY_RETRY_COUNT, cs.KEY_RETRY_DELAY, cs.KEY_LOG_RETENTION, cs.KEY_SCHEDULE_ENABLED,
               cs.KEY_SCHEDULE_DAYS, cs.KEY_SCHEDULE_TIME, cs.KEY_EMAIL_ENABLED, cs.KEY_EMAIL_RECIPIENT,
               cs.KEY_EMAIL_CONNECTION):
        assert key in cs.OWNED_KEYS


# --- retry count/delay generalisation (transfer.py / sync.py) -----------------------------------------------------------------

def test_a_transient_download_failure_is_retried_up_to_the_configured_count(monkeypatch):
    monkeypatch.setattr(transfer, "RETRY_COUNT", 3)
    monkeypatch.setattr(transfer, "RETRY_DELAY", 0)
    from ledsync.services.storage import StorageError
    calls = []

    def action():
        calls.append(1)
        if len(calls) <= 3:
            raise StorageError(exceptions.STORAGE_CONNECTIVITY, "busy")
        return "ok"
    assert transfer._retry(action) == "ok" and len(calls) == 4                       # first try + 3 retries


def test_retries_stop_after_the_configured_count_is_exhausted(monkeypatch):
    monkeypatch.setattr(transfer, "RETRY_COUNT", 1)
    monkeypatch.setattr(transfer, "RETRY_DELAY", 0)
    from ledsync.services.storage import StorageError
    calls = []

    def action():
        calls.append(1)
        raise StorageError(exceptions.STORAGE_CONNECTIVITY, "busy")
    with pytest.raises(StorageError):
        transfer._retry(action)
    assert len(calls) == 2                                                            # first try + 1 retry, then it gives up


def test_zero_retries_means_the_first_failure_is_final(monkeypatch):
    monkeypatch.setattr(transfer, "RETRY_COUNT", 0)
    monkeypatch.setattr(transfer, "RETRY_DELAY", 0)
    from ledsync.services.storage import StorageError
    calls = []

    def action():
        calls.append(1)
        raise StorageError(exceptions.STORAGE_CONNECTIVITY, "busy")
    with pytest.raises(StorageError):
        transfer._retry(action)
    assert len(calls) == 1


def test_sync_push_retries_use_the_same_shared_retry_count(monkeypatch):
    from ledsync.services import localfiles
    monkeypatch.setattr(transfer, "RETRY_COUNT", 2)
    monkeypatch.setattr(transfer, "RETRY_DELAY", 0)
    calls = []

    def action():
        calls.append(1)
        if len(calls) <= 2:
            raise localfiles.DeviceError(exceptions.NETWORK_DEVICE, "down")
        return "ok"
    assert sync._retry(action) == "ok" and len(calls) == 3


# --- Settings pages (web) --------------------------------------------------------------------------------------------------------

def test_the_new_settings_pages_need_login_and_the_launch_cookie(logged_in, client, launched):
    for path in ("/settings/download", "/settings/scheduling", "/settings/email", "/settings/application"):
        assert client.get(path).status_code == 403
        assert logged_in.get(path).status_code == 200


def test_download_settings_page_saves_and_shows_errors(logged_in):
    out = text_of(logged_in.post("/settings/download", data={"csrf_token": tok2(logged_in, "/settings/download"),
                                                              "retry_count": "2", "retry_delay": "3"},
                                 follow_redirects=True).get_data(as_text=True))
    assert "Download settings saved." in out
    bad = logged_in.post("/settings/download", data={"csrf_token": tok2(logged_in, "/settings/download"), "retry_count": "9"})
    assert bad.status_code == 400 and "retries" in text_of(bad.get_data(as_text=True))


def test_scheduling_settings_page_saves_days_and_shows_errors(logged_in):
    out = text_of(logged_in.post("/settings/scheduling", data={"csrf_token": tok2(logged_in, "/settings/scheduling"),
                                                                "enabled": "1", "days": ["Mon", "Wed"], "time": "03:15"},
                                 follow_redirects=True).get_data(as_text=True))
    assert "Scheduling settings saved." in out
    html = logged_in.get("/settings/scheduling").get_data(as_text=True)
    assert 'name="days" value="Mon" checked' in html and 'name="days" value="Wed" checked' in html
    assert 'name="days" value="Tue" checked' not in html and 'value="03:15"' in html
    bad = logged_in.post("/settings/scheduling", data={"csrf_token": tok2(logged_in, "/settings/scheduling"), "enabled": "1"})
    assert bad.status_code == 400


def test_email_settings_page_saves_and_never_echoes_the_connection_string(logged_in):
    secret = "endpoint=https://x.communication.azure.com/;accesskey=" + "B" * 40
    out = text_of(logged_in.post("/settings/email", data={"csrf_token": tok2(logged_in, "/settings/email"),
                                                           "enabled": "1", "recipient": "it@example.com",
                                                           "connection_string": secret}, follow_redirects=True).get_data(as_text=True))
    assert "Email settings saved." in out
    html = logged_in.get("/settings/email").get_data(as_text=True)
    assert secret not in html and "it@example.com" in html and "Saved" in html
    assert db_rows(logged_in.application.config["LEDSYNC"], "SELECT setting_value FROM application_settings "
                   "WHERE setting_name = 'email_connection_string'") == [{"setting_value": secret}]


def test_application_settings_page_shows_locations_and_saves_retention(logged_in):
    html = logged_in.get("/settings/application").get_data(as_text=True)
    assert "ledsync.db" in html and "ledsync.log" in html and "forever" in html
    out = text_of(logged_in.post("/settings/application", data={"csrf_token": tok2(logged_in, "/settings/application"),
                                                                 "retention": "45"}, follow_redirects=True).get_data(as_text=True))
    assert "Application settings saved." in out
    assert "45" in logged_in.get("/settings/application").get_data(as_text=True)
    bad = logged_in.post("/settings/application", data={"csrf_token": tok2(logged_in, "/settings/application"), "retention": "0"})
    assert bad.status_code == 400


def test_a_refused_settings_save_is_logged_as_invalid_configuration(logged_in):
    logged_in.post("/settings/download", data={"csrf_token": tok2(logged_in, "/settings/download"), "retry_count": "99"})
    rows = db_rows(logged_in.application.config["LEDSYNC"], "SELECT category, operation FROM exception_log")
    assert rows == [{"category": "Invalid configuration", "operation": "Save Download Settings"}]


def test_the_settings_nav_lists_every_section(logged_in):
    html = logged_in.get("/settings/cloud").get_data(as_text=True)
    for label in ("Cloud Storage", "Local Folders", "Download", "Scheduling", "Email", "Application"):
        assert label in html


# --- per-event, recurring daily timestamp cut-off (changes.py) -----------------------------------------------------------------

def test_cutoff_default_is_off_and_compares_everything(world_conn):
    conn = world_conn
    assert changes.load_cutoff_settings(conn, "1000") == changes.CutoffSettings(False, "")
    assert changes.cutoff_boundary(changes.load_cutoff_settings(conn, "1000")) is None


def test_save_cutoff_validates_is_per_event_and_needs_a_time_to_enable(world_conn):
    conn = world_conn
    conn.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('2000', 'y', 'g2')")
    conn.commit()
    with pytest.raises(changes.CutoffError):
        changes.save_cutoff_settings(conn, "1000", True, "")                     # enabling needs a time
    assert changes.save_cutoff_settings(conn, "1000", True, "21:00") is True
    assert changes.load_cutoff_settings(conn, "1000") == changes.CutoffSettings(True, "21:00")
    assert changes.load_cutoff_settings(conn, "2000") == changes.CutoffSettings(False, "")   # the other event is untouched
    assert changes.save_cutoff_settings(conn, "1000", True, "21:00") is False     # no change
    assert changes.save_cutoff_settings(conn, "1000", False, "21:00") is True     # disabled, time kept (not retyped)
    assert changes.load_cutoff_settings(conn, "1000") == changes.CutoffSettings(False, "21:00")
    for bad in ("25:00", "9:30", "21", "not a time"):
        with pytest.raises(changes.CutoffError):
            changes.save_cutoff_settings(conn, "1000", False, bad)
    with pytest.raises(changes.CutoffError):
        changes.save_cutoff_settings(conn, "9999", True, "21:00")                # unregistered event


def test_cutoff_boundary_is_the_most_recent_past_occurrence_of_the_time():
    from datetime import timezone
    settings = changes.CutoffSettings(True, "21:00")
    # "now" is after today's 21:00: the boundary is today's occurrence
    now = datetime(2026, 9, 22, 22, 30, tzinfo=timezone.utc)
    assert changes.cutoff_boundary(settings, now) == datetime(2026, 9, 22, 21, 0, tzinfo=timezone.utc)
    # "now" is before today's 21:00 has happened: the boundary is still yesterday's
    now = datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc)
    assert changes.cutoff_boundary(settings, now) == datetime(2026, 9, 21, 21, 0, tzinfo=timezone.utc)
    # exactly at the cut-off time: that instant counts as "already passed" (inclusive)
    now = datetime(2026, 9, 22, 21, 0, tzinfo=timezone.utc)
    assert changes.cutoff_boundary(settings, now) == now
    assert changes.cutoff_boundary(changes.CutoffSettings(False, "21:00"), now) is None    # disabled: no boundary
    assert changes.cutoff_boundary(changes.CutoffSettings(True, ""), now) is None          # no time set: no boundary


def _entry(path, status, ts, line=1):
    from ledsync.services.changelog import CloudEntry
    from ledsync.services.events import parse_timestamp
    return CloudEntry(line, line, path, ts, parse_timestamp(ts), status)


def _parsed(*entries):
    from ledsync.services.changelog import ParsedChangeLog
    return ParsedChangeLog(tuple(entries), (), "_ledassetschangelog.csv")


def test_decide_holds_new_work_after_the_boundary_but_leaves_already_done_files_alone():
    from ledsync.services.events import parse_timestamp
    boundary = parse_timestamp("2026-09-15T21:00:00Z")
    before = _entry("Table 1/Inner/before.png", "New", "2026-09-15T20:00:00Z")     # before the boundary: eligible
    after = _entry("Table 1/Inner/after.png", "New", "2026-09-15T22:00:00Z")       # after the boundary: held
    exactly = _entry("Table 1/Inner/exactly.png", "New", "2026-09-15T21:00:00Z")   # exactly on it: eligible (inclusive)
    struct = real_structure()
    out = changes.compare(_parsed(before, after, exactly), struct, {}, boundary)
    by_name = {a.file_name: a for a in out.assessments}
    assert by_name["before.png"].action == changes.DOWNLOAD
    assert by_name["exactly.png"].action == changes.DOWNLOAD
    assert by_name["after.png"].action == changes.SKIPPED_CUTOFF
    # an already-processed file held by an earlier boundary stays DONE once it is no longer held, not reclassified
    from ledsync.services.changes import LocalRecord
    local2 = {changes.path_key(1, "Inner", "before.png"): LocalRecord("Success", parse_timestamp("2026-09-15T20:30:00Z"))}
    out2 = changes.compare(_parsed(before), struct, local2, boundary)
    assert out2.assessments[0].action == changes.DONE


def test_a_file_held_by_the_boundary_is_downloaded_once_the_boundary_moves_past_it():
    """The held file from the previous test becomes eligible once a LATER boundary (the next day's) passes it -
    nothing about it is stored; it is simply reconsidered fresh on the next check."""
    from ledsync.services.events import parse_timestamp
    late = _entry("Table 1/Inner/late.png", "New", "2026-09-15T22:00:00Z")
    struct = real_structure()
    held = changes.compare(_parsed(late), struct, {}, parse_timestamp("2026-09-15T21:00:00Z"))
    assert held.assessments[0].action == changes.SKIPPED_CUTOFF
    next_day = changes.compare(_parsed(late), struct, {}, parse_timestamp("2026-09-16T21:00:00Z"))
    assert next_day.assessments[0].action == changes.DOWNLOAD


def test_a_deletion_after_the_boundary_keeps_the_local_copy_for_now():
    from ledsync.services.changes import LocalRecord
    from ledsync.services.events import parse_timestamp
    boundary = parse_timestamp("2026-09-15T21:00:00Z")
    deleted = _entry("Table 1/Inner/a.png", "Deleted", "2026-09-15T22:00:00Z")       # after the boundary
    local = {changes.path_key(1, "Inner", "a.png"): LocalRecord("Success", parse_timestamp("2026-09-01T00:00:00Z"))}
    out = changes.compare(_parsed(deleted), real_structure(), local, boundary)
    assert out.assessments[0].action == changes.SKIPPED_CUTOFF


def test_the_boundary_also_applies_to_azure_only_unlogged_files(monkeypatch):
    from ledsync.services.events import parse_timestamp
    from ledsync.services.storage import BlobInfo, EventLocation

    class FakeStorage:
        def list_files(self, location, folder, listings=None):
            if folder == "Table 1/Inner":
                return [BlobInfo("Table 1/Inner/late.png", 10, None, "2026-09-15T22:00:00Z", "etag")]
            return []
    boundary = parse_timestamp("2026-09-15T21:00:00Z")
    struct = real_structure()
    base = changes.compare(_parsed(), struct, {})
    out = changes.add_azure_files(base, FakeStorage(), EventLocation("2026", "1000 - x"), struct, {}, boundary)
    assert [a.action for a in out.assessments if a.file_name == "late.png"] == [changes.SKIPPED_CUTOFF]


def test_check_event_uses_the_saved_cutoff_and_counts_it_in_the_summary(ev, cfg):
    from datetime import timedelta, timezone
    from phase6_helpers import csv_text
    from test_phase8_sync import put
    put(ev, "Table 1/inner/a.png", b"inner a")
    # a boundary is always within the last 24h of "now"; a change dated two days ahead is after it regardless of when
    # this test happens to run, so it is reliably held (an ordinary changelog entry is never really future-dated -
    # this only exercises the mechanism, and a future-dated entry is reported separately, not blocked, either way).
    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    put(ev, "_ledassetschangelog.csv", csv_text([("Table 1/Inner/a.png", future, "New")]))
    conn = connect(cfg.db_path)
    changes.save_cutoff_settings(conn, "1000", True, "12:00")
    conn.close()
    out = text_of(ev.post("/events/1000/changes/check", data={"csrf_token": tok(ev, "/events/1000/changes")},
                          follow_redirects=True).get_data(as_text=True))
    assert "held back by the timestamp cut-off" in text_of(
        db_rows(cfg, "SELECT message FROM operation_log WHERE operation = 'Check Changes'")[-1]["message"])
    assert "Held (After Cut" in out or "held" in out.lower()


# --- the cut-off form on the Device Mapping page ------------------------------------------------------------------------------

def test_the_cutoff_form_saves_validates_and_needs_csrf_and_login(ev, cfg, client, launched):
    assert client.post("/events/1000/cutoff", data={"cutoff_enabled": "1", "cutoff_time": "21:00"}).status_code == 403
    out = text_of(ev.post("/events/1000/cutoff", data={"csrf_token": tok(ev, "/events/1000"), "cutoff_enabled": "1",
                                                        "cutoff_time": "21:00"}, follow_redirects=True).get_data(as_text=True))
    assert "Timestamp cut-off saved." in out
    html = ev.get("/events/1000").get_data(as_text=True)
    assert 'value="21:00"' in html and 'name="cutoff_enabled" value="1" checked' in html
    bad = ev.post("/events/1000/cutoff", data={"csrf_token": tok(ev, "/events/1000"), "cutoff_time": "nonsense"})
    assert bad.status_code == 400 and "24-hour UTC" in text_of(bad.get_data(as_text=True))
    without_time = ev.post("/events/1000/cutoff", data={"csrf_token": tok(ev, "/events/1000"), "cutoff_enabled": "1"})
    assert without_time.status_code == 400 and "before turning it on" in text_of(without_time.get_data(as_text=True))
    assert ev.post("/events/9999/cutoff", data={"csrf_token": tok(ev, "/events/1000"), "cutoff_time": ""}).status_code == 404
    # unchecking the box turns it off but keeps the time (no need to retype it)
    off = text_of(ev.post("/events/1000/cutoff", data={"csrf_token": tok(ev, "/events/1000"), "cutoff_time": "21:00"},
                          follow_redirects=True).get_data(as_text=True))
    assert "Timestamp cut-off saved." in off
    html = ev.get("/events/1000").get_data(as_text=True)
    assert 'value="21:00"' in html and 'name="cutoff_enabled" value="1" checked' not in html


# --- startup log retention pruning -------------------------------------------------------------------------------------------------

def test_startup_prunes_logs_older_than_the_retention_setting(cfg):
    from datetime import datetime, timedelta, timezone
    from ledsync.main import _seed_and_log_startup
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    cs.save_log_retention(conn, "7")
    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    oplog.record(conn, "Login", "Success", "old one")
    conn.execute("UPDATE operation_log SET timestamp = ? WHERE message = 'old one'", (old,))
    oplog.record(conn, "Login", "Success", "recent one")
    conn.execute("UPDATE operation_log SET timestamp = ? WHERE message = 'recent one'", (recent,))
    exceptions.record(conn, exceptions.PERMISSION, "Op", "old exception")
    conn.execute("UPDATE exception_log SET timestamp = ?", (old,))
    conn.commit()
    conn.close()
    _seed_and_log_startup(cfg)
    conn = connect(cfg.db_path)
    messages = [r["message"] for r in conn.execute("SELECT message FROM operation_log")]
    assert "old one" not in messages and "recent one" in messages
    assert conn.execute("SELECT COUNT(*) FROM exception_log").fetchone()[0] == 0
    assert any("Log retention" in m for m in messages)
    conn.close()


def test_startup_never_prunes_when_retention_is_forever(cfg):
    from ledsync.main import _seed_and_log_startup
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    oplog.record(conn, "Login", "Success", "keep me")
    conn.execute("UPDATE operation_log SET timestamp = '2000-01-01T00:00:00Z' WHERE message = 'keep me'")
    conn.commit()
    conn.close()
    _seed_and_log_startup(cfg)
    rows = db_rows(cfg, "SELECT message FROM operation_log")
    assert any(r["message"] == "keep me" for r in rows)


# --- Dashboard Actions column (Step 10.2) --------------------------------------------------------------------------------------

def test_the_dashboard_row_offers_test_connections_and_view_logs(ev, cfg, tmp_path_factory):
    devices = map_devices(ev, cfg, tmp_path_factory)
    html = ev.get("/").get_data(as_text=True)
    assert 'action="/events/1000/mappings"' in html and 'value="test-all"' in html
    assert 'href="/logs?event=1000"' in html
    out = text_of(ev.post("/events/1000/mappings", data={"csrf_token": tok(ev, "/"), "action": "test-all"},
                          follow_redirects=True).get_data(as_text=True))
    assert "Connection Successful" in out or "connections" in out.lower()
    assert db_rows(cfg, "SELECT connection_status FROM led_mappings WHERE shared_folder != ''")[0]["connection_status"] == "Connection Successful"


def test_view_logs_from_the_dashboard_filters_to_that_event(ev, cfg):
    conn = connect(cfg.db_path)
    oplog.record(conn, "Login", "Success", "for 1000", "1000")
    conn.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('2000', 'y', 'g2')")
    oplog.record(conn, "Login", "Success", "for 2000", "2000")
    conn.commit()
    conn.close()
    out = text_of(ev.get("/logs?event=1000").get_data(as_text=True))
    assert "for 1000" in out and "for 2000" not in out


# --- fixtures ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def world_conn(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    conn.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('1000', 'x', 'g')")
    conn.commit()
    yield conn
    conn.close()


# --- independent-review regression: retries/delay are ordinary parameters, never a shared mutable global -------------------------

def test_retry_and_delay_are_passed_explicitly_never_via_a_shared_global(monkeypatch):
    """Two concurrent calls with DIFFERENT retries/delay must never see or leave behind each other's value - proven by
    running them on separate threads at the same time and checking the module constants are untouched throughout."""
    from ledsync.services.storage import StorageError
    monkeypatch.setattr(transfer, "RETRY_COUNT", 1)               # the untouched module defaults, watched throughout
    monkeypatch.setattr(transfer, "RETRY_DELAY", 0.2)
    seen_a, seen_b = [], []
    start = threading.Event()

    def worker(retries, delay, seen, marker):
        start.wait(2)
        calls = []

        def action():
            calls.append(1)
            seen.append((transfer.RETRY_COUNT, transfer.RETRY_DELAY))       # the untouched globals, sampled mid-flight
            if len(calls) <= retries:
                raise StorageError(exceptions.STORAGE_CONNECTIVITY, "busy")
            return marker
        assert transfer._retry(action, retries, delay) == marker
        assert len(calls) == retries + 1

    ta = threading.Thread(target=worker, args=(4, 0.01, seen_a, "a"))
    tb = threading.Thread(target=worker, args=(1, 0.01, seen_b, "b"))
    ta.start(), tb.start()
    start.set()
    ta.join(5), tb.join(5)
    assert not ta.is_alive() and not tb.is_alive()
    # the module defaults were never touched by either call, on either thread, at any point
    assert set(seen_a) <= {(1, 0.2)} and set(seen_b) <= {(1, 0.2)}
    assert (transfer.RETRY_COUNT, transfer.RETRY_DELAY) == (1, 0.2)


def test_run_job_passes_the_saved_retry_setting_down_as_a_plain_argument_never_a_global(ev, cfg, tmp_path_factory, monkeypatch):
    """`_run_job` must load Settings -> Download once and pass it down as an ordinary argument to every engine -
    never assign it to `transfer.RETRY_COUNT` / `RETRY_DELAY`, which a concurrent job on another thread could also
    be reading or restoring."""
    from test_phase8_sync import seed, sync_all
    conn = connect(cfg.db_path)
    cs.save_download_settings(conn, "3", "0")
    conn.commit()
    conn.close()
    map_devices(ev, cfg, tmp_path_factory)
    seed(ev)
    before = (transfer.RETRY_COUNT, transfer.RETRY_DELAY)
    seen = []
    real = sync.process

    def watching(*args, **kwargs):
        seen.append((transfer.RETRY_COUNT, transfer.RETRY_DELAY))         # sampled mid-run, from inside the job
        return real(*args, **kwargs)
    monkeypatch.setattr(sync, "process", watching)
    sync_all(ev)
    assert seen and set(seen) == {before}                                # the module defaults were never touched
    assert (transfer.RETRY_COUNT, transfer.RETRY_DELAY) == before         # ... and are still untouched afterwards


def test_a_malformed_cutoff_time_is_refused_with_a_plain_message(world_conn):
    conn = world_conn
    for bad in ("25:00", "12:60", "9:30", "9am", "not a time at all", "21:00:00"):
        with pytest.raises(changes.CutoffError, match="24-hour UTC"):
            changes.save_cutoff_settings(conn, "1000", False, bad)


def test_the_cutoff_field_shows_a_live_utc_clock_and_a_local_time_preview(ev):
    html = ev.get("/events/1000").get_data(as_text=True)
    assert "data-utc-clock" in html and "enter in <strong>UTC</strong>" in html
    assert 'data-utc-time-preview="cutoff-time-local"' in html and 'data-utc-time-target' in html
