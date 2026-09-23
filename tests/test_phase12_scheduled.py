"""Phase 12 - Scheduled Operation: the single-instance lock, Windows Task Scheduler registration
(services/scheduler.py), the headless scheduled run (scheduled_run.py), and the Settings ->
Scheduling page's new Windows-account fields."""

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import csrf_from, db_rows

from ledsync.db import connect, init_db
from ledsync.services import scheduler, settings as cs, singleinstance


def tok(client, path="/"):
    return csrf_from(client, path)


def text_of(html):
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# --- single-instance lock -----------------------------------------------------------------------------------------

def test_a_second_lock_attempt_fails_immediately_without_blocking(cfg):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with singleinstance.instance_lock(cfg.data_dir):
        with pytest.raises(singleinstance.AlreadyRunning):
            with singleinstance.instance_lock(cfg.data_dir):
                pass                                            # pragma: no cover - must never be reached


def test_the_lock_is_released_when_the_with_block_exits(cfg):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with singleinstance.instance_lock(cfg.data_dir):
        pass
    with singleinstance.instance_lock(cfg.data_dir):             # a second, later acquisition succeeds
        pass


def test_the_lock_file_is_created_automatically(cfg):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    assert not (cfg.data_dir / singleinstance.LOCK_FILENAME).exists()
    with singleinstance.instance_lock(cfg.data_dir):
        assert (cfg.data_dir / singleinstance.LOCK_FILENAME).exists()


# --- Task Scheduler XML / registration (services/scheduler.py) -----------------------------------------------------

def test_task_xml_has_the_right_days_time_command_and_escapes_the_username():
    xml = scheduler._task_xml("DOMAIN\\a&b<user>", ("Mon", "Wed", "Fri"), "02:30", "C:\\app", "C:\\data")
    assert "<Monday />" in xml and "<Wednesday />" in xml and "<Friday />" in xml
    assert "<Tuesday />" not in xml and "<Sunday />" not in xml
    assert "T02:30:00" in xml
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml       # owner decision: catch up when possible
    assert "<LogonType>Password</LogonType>" in xml                    # owner decision: works with nobody logged in
    assert sys.executable in xml
    assert "-m ledsync.scheduled_run" in xml
    assert '--data-dir "C:\\data"' in xml                              # review fix: baked in, account-independent
    assert "DOMAIN\\a&amp;b&lt;user&gt;" in xml                        # XML-escaped, not raw
    assert "<user>" not in xml and "a&b" not in xml


class FakeRunner:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        return type("Result", (), {"returncode": self.returncode, "stdout": "", "stderr": "secret-ish output"})()


def test_register_calls_schtasks_create_with_the_expected_arguments():
    fake = FakeRunner()
    scheduler.register("me", "hunter2", ("Mon",), "02:30", "C:\\app", "C:\\data", runner=fake)
    assert len(fake.calls) == 1
    args = fake.calls[0]
    assert args[:3] == ["schtasks", "/Create", "/TN"]
    assert scheduler.TASK_NAME in args
    assert "/RU" in args and "me" in args
    assert "/RP" in args and "hunter2" in args
    assert "/F" in args
    xml_index = args.index("/XML") + 1
    import os
    assert not os.path.exists(args[xml_index])                        # the temp XML file is cleaned up after


def test_register_raises_on_a_nonzero_return_code_without_leaking_schtasks_output():
    fake = FakeRunner(returncode=1)
    with pytest.raises(scheduler.SchedulerError) as exc:
        scheduler.register("me", "hunter2", ("Mon",), "02:30", "C:\\app", "C:\\data", runner=fake)
    assert "hunter2" not in str(exc.value) and "secret-ish output" not in str(exc.value)


def test_unregister_calls_delete_and_never_raises_when_nothing_is_registered():
    fake = FakeRunner(returncode=1)                                    # schtasks' own "not found" result
    scheduler.unregister(runner=fake)                                  # must not raise
    assert fake.calls[0][:3] == ["schtasks", "/Delete", "/TN"]


def test_is_registered_reflects_the_query_return_code():
    assert scheduler.is_registered(runner=FakeRunner(returncode=0)) is True
    assert scheduler.is_registered(runner=FakeRunner(returncode=1)) is False


# --- Settings -> Scheduling: the new Windows-account fields (services/settings.py) -----------------------------------

def test_schedule_username_is_validated_and_required_to_enable(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    with pytest.raises(cs.SettingsError):
        cs.save_schedule(conn, True, ["Mon"], "02:30", "")
    with pytest.raises(cs.SettingsError):
        cs.save_schedule(conn, False, [], "", "sk_live_" + "A1b2C3d4" * 6)   # key-shaped value refused
    changed = cs.save_schedule(conn, True, ["Mon"], "02:30", "DOMAIN\\op")
    assert "account" in changed
    assert cs.load_schedule(conn).username == "DOMAIN\\op"
    conn.close()


# --- the Settings -> Scheduling route registers/removes the real task (fake schtasks throughout) --------------------

def test_enabling_the_schedule_registers_the_task_and_saves(logged_in, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "register", lambda *a, **kw: calls.append((a, kw)))
    out = text_of(logged_in.post("/settings/scheduling", data={
        "csrf_token": tok(logged_in, "/settings/scheduling"), "enabled": "1", "days": ["Mon", "Wed"],
        "time": "03:15", "username": "DOMAIN\\op", "password": "hunter2",
    }, follow_redirects=True).get_data(as_text=True))
    assert "Scheduling settings saved." in out
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "DOMAIN\\op" and args[1] == "hunter2"
    html = logged_in.get("/settings/scheduling").get_data(as_text=True)
    assert 'name="days" value="Mon" checked' in html and 'name="days" value="Wed" checked' in html
    assert 'value="DOMAIN\\op"' in html or "DOMAIN\\op" in html


def test_enabling_without_a_password_is_refused_before_touching_the_scheduler(logged_in, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "register", lambda *a, **kw: calls.append((a, kw)))
    resp = logged_in.post("/settings/scheduling", data={
        "csrf_token": tok(logged_in, "/settings/scheduling"), "enabled": "1", "days": ["Mon"],
        "time": "03:15", "username": "DOMAIN\\op",
    })
    assert resp.status_code == 400
    assert calls == []                                                # never even attempted


def test_a_failed_registration_refuses_the_save_and_leaves_the_schedule_off(logged_in, monkeypatch, cfg):
    def boom(*a, **kw):
        raise scheduler.SchedulerError("Windows refused the account/password.")
    monkeypatch.setattr(scheduler, "register", boom)
    resp = logged_in.post("/settings/scheduling", data={
        "csrf_token": tok(logged_in, "/settings/scheduling"), "enabled": "1", "days": ["Mon"],
        "time": "03:15", "username": "DOMAIN\\op", "password": "wrong",
    })
    assert resp.status_code == 400
    assert "Windows refused" in text_of(resp.get_data(as_text=True))
    assert db_rows(cfg, "SELECT setting_value FROM application_settings WHERE setting_name = 'schedule_enabled'") == []


def test_disabling_the_schedule_removes_the_task(logged_in, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "unregister", lambda **kw: calls.append(kw))
    out = text_of(logged_in.post("/settings/scheduling", data={
        "csrf_token": tok(logged_in, "/settings/scheduling"), "time": "", "username": "",
    }, follow_redirects=True).get_data(as_text=True))
    assert "saved" in out.lower()
    assert len(calls) == 1


def test_a_failed_unregister_is_refused_and_logged_not_silently_500d(logged_in, monkeypatch):
    """Review fix: unregister() is now guarded the same way register() always was - a SchedulerError
    here must surface as an ordinary refused save, not an uncaught exception / generic error page."""
    def boom(**kw):
        raise scheduler.SchedulerError("Could not remove the scheduled task.")
    monkeypatch.setattr(scheduler, "unregister", boom)
    resp = logged_in.post("/settings/scheduling", data={
        "csrf_token": tok(logged_in, "/settings/scheduling"), "time": "", "username": "",
    })
    assert resp.status_code == 400
    assert "Could not remove" in text_of(resp.get_data(as_text=True))


def test_a_database_failure_after_a_successful_registration_rolls_back_the_task(logged_in, monkeypatch):
    """Review fix: if Task Scheduler registration succeeds but the settings save itself then fails,
    the real Task Scheduler entry must not be left registered while the database disagrees."""
    register_calls, unregister_calls = [], []
    monkeypatch.setattr(scheduler, "register", lambda *a, **kw: register_calls.append((a, kw)))
    monkeypatch.setattr(scheduler, "unregister", lambda **kw: unregister_calls.append(kw))

    from ledsync.services import settings as cs

    def boom(*a, **kw):
        raise cs.SettingsError("The settings could not be saved. Try again.")
    monkeypatch.setattr(cs, "save_schedule", boom)

    resp = logged_in.post("/settings/scheduling", data={
        "csrf_token": tok(logged_in, "/settings/scheduling"), "enabled": "1", "days": ["Mon"],
        "time": "03:15", "username": "DOMAIN\\op", "password": "hunter2",
    })
    assert resp.status_code == 400
    assert len(register_calls) == 1                                   # it did try to register first
    assert len(unregister_calls) == 1                                  # ... then rolled it back on the DB failure


def test_the_page_shows_whether_the_task_is_actually_registered(logged_in, monkeypatch):
    monkeypatch.setattr(scheduler, "is_registered", lambda **kw: True)
    assert "Registered" in logged_in.get("/settings/scheduling").get_data(as_text=True)
    monkeypatch.setattr(scheduler, "is_registered", lambda **kw: False)
    assert "Not registered" in logged_in.get("/settings/scheduling").get_data(as_text=True)


# --- the headless run itself (scheduled_run.py) ---------------------------------------------------------------------

def test_scheduled_run_never_imports_the_ui_toolkit():
    code = "import sys, ledsync.scheduled_run; print('webview' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_data_dir_argument_is_authoritative_regardless_of_the_running_account(cfg, monkeypatch):
    """Independent review finding (Blocker): a scheduled run's usual %LOCALAPPDATA%-based folder
    resolution is scoped to whichever Windows account actually runs the Task - not necessarily the
    account that registered it (Addendum A 39.6 lets the operator choose a dedicated account on
    purpose). --data-dir must override that resolution entirely, so the same venue data is found no
    matter which account Task Scheduler runs the Task as."""
    from ledsync import scheduled_run
    monkeypatch.delenv("LEDSYNC_DATA_DIR", raising=False)              # prove it does NOT rely on the env var
    result = scheduled_run._load_config(["--data-dir", str(cfg.data_dir)])
    assert result.data_dir == cfg.data_dir


def test_register_bakes_the_registering_sessions_data_dir_into_the_task(logged_in, monkeypatch, cfg):
    """The Settings page must pass ITS OWN real data folder to scheduler.register(), not rely on the
    scheduled process resolving one for itself later."""
    calls = []
    monkeypatch.setattr(scheduler, "register", lambda *a, **kw: calls.append(a))
    logged_in.post("/settings/scheduling", data={
        "csrf_token": tok(logged_in, "/settings/scheduling"), "enabled": "1", "days": ["Mon"],
        "time": "03:15", "username": "DOMAIN\\op", "password": "hunter2",
    })
    assert len(calls) == 1
    positional = calls[0]
    assert Path(positional[-1]) == cfg.data_dir                        # data_dir is the last positional arg


@pytest.fixture
def headless(cfg, monkeypatch):
    """scheduled_run.main() calls config.load() itself, which only honours LEDSYNC_DATA_DIR (an
    env var), not the `cfg` fixture's Config object directly - point it at the same scratch folder."""
    monkeypatch.setenv("LEDSYNC_DATA_DIR", str(cfg.data_dir))
    return cfg


def test_no_registered_events_logs_success_and_does_nothing(headless):
    cfg = headless
    from ledsync import scheduled_run
    init_db(cfg.db_path)
    assert scheduled_run.main([]) == 0
    rows = db_rows(cfg, "SELECT status, message FROM operation_log WHERE operation = 'Scheduled Run'")
    assert [r["status"] for r in rows] == ["Started", "Success"]
    assert "No events are registered" in rows[1]["message"]


def test_cloud_not_configured_logs_failed_and_runs_nothing(headless, monkeypatch):
    cfg = headless
    from ledsync import scheduled_run
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    conn.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('1000', 'x', 'g')")
    conn.commit()
    conn.close()
    calls = []
    monkeypatch.setattr(scheduled_run.downloads, "run_job", lambda *a, **kw: calls.append(a))
    assert scheduled_run.main([]) == 1
    rows = db_rows(cfg, "SELECT status, message FROM operation_log WHERE operation = 'Scheduled Run'")
    assert rows[-1]["status"] == "Failed" and "Cloud Storage is not configured" in rows[-1]["message"]
    assert calls == []


def test_one_bad_event_never_stops_the_others(headless, monkeypatch):
    cfg = headless
    from ledsync import scheduled_run
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    conn.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('1000', 'a', 'g1')")
    conn.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('2000', 'b', 'g2')")
    cs.save_cloud(conn, "acc", "2026", "A" * 88)
    conn.close()

    seen = []

    def fake_run_job(cfgobj, factory, settings, event_id, progress, tz=None, **kw):
        seen.append(event_id)
        if event_id == "1000":
            raise RuntimeError("boom")
    monkeypatch.setattr(scheduled_run.downloads, "run_job", fake_run_job)

    assert scheduled_run.main([]) == 0
    assert seen == ["1000", "2000"]                                    # the second event still ran
    rows = db_rows(cfg, "SELECT status FROM operation_log WHERE operation = 'Scheduled Run'")
    assert [r["status"] for r in rows] == ["Started", "Success"]        # the wrapper itself still reports success


def test_a_run_is_skipped_and_logged_blocked_when_another_instance_holds_the_lock(headless):
    cfg = headless
    from ledsync import scheduled_run
    init_db(cfg.db_path)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with singleinstance.instance_lock(cfg.data_dir):
        assert scheduled_run.main([]) == 0
    rows = db_rows(cfg, "SELECT status, message FROM operation_log WHERE operation = 'Scheduled Run'")
    assert rows and rows[-1]["status"] == "Blocked"
    assert "already using this data folder" in rows[-1]["message"]
