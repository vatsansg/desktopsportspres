"""Phase 7 - the background download job, its routes and the page (Steps 7.1 - 7.3 through the real app)."""

import csv
import re
import time

import pytest
from conftest import csrf_from, db_rows, serve_event
from phase6_helpers import REAL_GUID_JSON, T0, T1, csv_text

from ledsync.db import connect
from ledsync.services import downloads, transfer
from ledsync.services.progress import Progress

FOLDER = "1000 - Star contender Doha"
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 20


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch):
    monkeypatch.setattr(transfer, "RETRY_DELAY", 0)


@pytest.fixture
def ev(logged_in):
    serve_event(logged_in.application.extensions["test.azure"], dict(REAL_GUID_JSON))
    logged_in.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(logged_in, "/events/new")})
    return logged_in


def azure_of(client):
    return client.application.extensions["test.azure"]


def put(client, path, data=PNG):
    azure_of(client).put("2026", f"{FOLDER}/{path}", data)


def put_log(client, rows):
    put(client, "_ledassetschangelog.csv", csv_text(rows))


def token(client):
    return csrf_from(client, "/events/1000/changes")


def page(client):
    return client.get("/events/1000/changes").get_data(as_text=True)


def check(client):
    return client.post("/events/1000/changes/check", data={"csrf_token": token(client)}, follow_redirects=True)


def download(client):
    return client.post("/events/1000/changes/download", data={"csrf_token": token(client)}, follow_redirects=True)


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def history(cfg):
    return db_rows(cfg, "SELECT table_number, led_type, file_name, status FROM download_history ORDER BY download_id")


def seed(ev):
    put(ev, "rpi/HOME_Look.png", b"rpi bytes")
    put(ev, "Table 1/inner/a.png", b"inner a")
    put(ev, "Table 1/outer/b.png", b"outer b")
    put(ev, "Table 1/mainled/c.png", b"main c")
    put(ev, "Table 2/inner/d.png", b"table two d")
    put_log(ev, [("RPI/HOME_Look.png", T1, "New"), ("Table 1/Inner/a.png", T1, "New"), ("Table 1/Outer/b.png", T1, "New"),
                 ("Table 1/MainLED/c.png", T1, "New")])


# --- the whole thing through the page ------------------------------------------------------------------------------------------

def test_checking_then_downloading_puts_every_file_in_its_place(ev, cfg):
    seed(ev)
    check(ev)
    html = page(ev)
    assert "DOWNLOAD FILES (5)" in html and html.count("btn-primary") == 1          # 4 logged + 1 only in Azure (Table 2)
    assert "Nothing is sent to the LED devices yet" in text_of(html) or "nothing is sent to the LED devices yet" in text_of(html)
    out = download(ev)
    text = text_of(out.get_data(as_text=True))
    assert "Downloaded 5 file(s), removed 0, failed 0." in text
    events = cfg.data_dir / "Events" / "1000"
    assert sorted(str(p.relative_to(cfg.data_dir)).replace("\\", "/") for p in cfg.data_dir.rglob("*") if p.is_file() and p.suffix == ".png") == \
        ["Events/1000/Table 1/Inner/a.png", "Events/1000/Table 1/Main LED/c.png", "Events/1000/Table 1/Outer/b.png",
         "Events/1000/Table 2/Inner/d.png", "RPI/1000/HOME_Look.png"]
    assert (events / "Table 2" / "Inner" / "d.png").read_bytes() == b"table two d"
    assert "Nothing is waiting to be downloaded." in page(ev) and "CHECK FOR CHANGES" in page(ev)


def test_the_summary_is_shown_once(ev):
    seed(ev)
    check(ev)
    assert "Downloaded 5 file(s)" in text_of(download(ev).get_data(as_text=True))
    assert "Downloaded 5 file(s)" not in text_of(page(ev))


def test_history_and_the_local_change_log_file_cover_every_kind_of_file(ev, cfg):
    seed(ev)
    check(ev)
    download(ev)
    assert sorted(history(cfg), key=lambda h: h["file_name"].casefold()) == [
        {"table_number": 1, "led_type": "Inner", "file_name": "a.png", "status": "Success"},
        {"table_number": 1, "led_type": "Outer", "file_name": "b.png", "status": "Success"},
        {"table_number": 1, "led_type": "MainLED", "file_name": "c.png", "status": "Success"},
        {"table_number": 2, "led_type": "Inner", "file_name": "d.png", "status": "Success"},
        {"table_number": None, "led_type": "RPI", "file_name": "HOME_Look.png", "status": "Success"}]
    rows = list(csv.reader(open(cfg.data_dir / "_localchangelog.csv", encoding="utf-8-sig", newline="")))
    got = sorted((r[2], r[3], r[4], r[9]) for r in rows[1:])
    assert got == [("", "RPI", "HOME_Look.png", "Success"), ("Table 1", "Inner", "a.png", "Success"),
                   ("Table 1", "Main LED", "c.png", "Success"), ("Table 1", "Outer", "b.png", "Success"),
                   ("Table 2", "Inner", "d.png", "Success")]


def test_the_check_shows_the_files_found_only_in_azure(ev):
    seed(ev)
    text = text_of(check(ev).get_data(as_text=True))
    assert "New, Not In Log 1" in text and "In Azure but not in the change log." in text and "d.png" in text


def test_a_check_and_a_download_change_nothing_they_should_not(ev, cfg):
    seed(ev)
    check(ev)
    download(ev)
    assert set(azure_of(ev).calls) <= {"list_containers", "list", "download"}         # only reads, ever
    assert not (cfg.data_dir / "Events" / "1000" / "Table 2" / "Outer").exists()


def test_a_second_download_finds_nothing_to_do(ev, cfg):
    seed(ev)
    check(ev)
    download(ev)
    n = len(history(cfg))
    out = text_of(download(ev).get_data(as_text=True))
    assert len(history(cfg)) == n and "Downloaded 0 file(s), removed 0, failed 0." in out


def test_the_chosen_folders_are_used(ev, cfg, tmp_path_factory):
    assets_dir, rpi_dir = tmp_path_factory.mktemp("A") / "Events", tmp_path_factory.mktemp("R") / "RPI"
    ev.post("/settings/folders", data={"asset_folder": str(assets_dir), "rpi_folder": str(rpi_dir),
                                       "csrf_token": csrf_from(ev, "/settings/folders")})
    seed(ev)
    check(ev)
    download(ev)
    assert (assets_dir / "1000" / "Table 1" / "Inner" / "a.png").read_bytes() == b"inner a"
    assert (rpi_dir / "1000" / "HOME_Look.png").read_bytes() == b"rpi bytes"
    assert not (cfg.data_dir / "Events").exists() and not (cfg.data_dir / "RPI").exists()


def test_removals_in_the_cloud_delete_local_files_and_are_reported(ev, cfg):
    seed(ev)
    check(ev)
    download(ev)
    put_log(ev, [("Table 1/Inner/a.png", T0, "New"), ("Table 1/Inner/a.png", "2026-09-18T00:00:00Z", "Deleted")])
    check(ev)
    text = text_of(download(ev).get_data(as_text=True))
    assert "removed 1" in text and not (cfg.data_dir / "Events" / "1000" / "Table 1" / "Inner" / "a.png").exists()


def test_problems_with_single_files_are_listed_and_the_rest_arrive(ev, cfg):
    seed(ev)
    put_log(ev, [("Table 1/Inner/a.png", T1, "New"), ("Table 1/Inner/ghost.png", T1, "New")])
    check(ev)
    text = text_of(download(ev).get_data(as_text=True))
    assert "failed 1" in text and "ghost.png" in text and "was not found" in text
    assert (cfg.data_dir / "Events" / "1000" / "Table 1" / "Inner" / "a.png").exists()


def test_an_unusable_asset_folder_is_a_plain_message(ev, cfg, tmp_path_factory):
    blocker = tmp_path_factory.mktemp("b") / "file.txt"
    blocker.write_text("x")
    ev.post("/settings/folders", data={"asset_folder": str(blocker / "Events"), "rpi_folder": "",
                                       "csrf_token": csrf_from(ev, "/settings/folders")})
    seed(ev)
    check(ev)
    text = text_of(download(ev).get_data(as_text=True))
    assert "could not be created" in text and not [h for h in history(cfg) if h["table_number"] == 1]


def test_when_the_event_was_exported_again_nothing_is_downloaded(ev, cfg):
    seed(ev)
    check(ev)
    serve_event(azure_of(ev), dict(REAL_GUID_JSON, exportGuid="7c9e6679-7425-40de-944b-e07fc1f90ae7"))
    text = text_of(download(ev).get_data(as_text=True))
    assert "exported this event again" in text and history(cfg) == []


# --- access and safety --------------------------------------------------------------------------------------------------------

def test_the_new_routes_need_login_the_launch_cookie_and_csrf(ev, client, launched):
    for path in ("download", "cancel"):
        assert ev.post(f"/events/1000/changes/{path}").status_code == 403               # no CSRF token
        assert client.post(f"/events/1000/changes/{path}").status_code == 403           # no launch cookie
    assert client.get("/events/1000/changes/progress").status_code == 403
    assert ev.post("/events/9999/changes/download", data={"csrf_token": token(ev)}).status_code == 404
    assert ev.get("/events/9999/changes/progress").status_code == 404


def test_signed_out_requests_go_to_login(launched):
    assert "/login" in launched.get("/events/1000/changes/progress").headers["Location"]
    tok = csrf_from(launched, "/login")
    assert "/login" in launched.post("/events/1000/changes/download", data={"csrf_token": tok}).headers["Location"]


def test_downloading_without_cloud_settings_says_so_and_contacts_nothing(ev, monkeypatch):
    seed(ev)
    for name in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY"):
        monkeypatch.delenv(name)
    calls = len(azure_of(ev).calls)
    assert "Cloud storage is not set up yet" in download(ev).get_data(as_text=True)
    assert len(azure_of(ev).calls) == calls


def test_a_get_never_starts_a_download(ev, cfg):
    seed(ev)
    check(ev)
    for _ in range(3):
        ev.get("/events/1000/changes")
        ev.get("/events/1000/changes/progress")
    assert history(cfg) == [] and not (cfg.data_dir / "Events").exists()


# --- the job itself: threads, exclusivity, progress, cancel -------------------------------------------------------------------

def test_a_real_background_job_runs_to_completion_and_can_be_polled(ev, cfg):
    ev.application.config["SYNC_JOBS"] = False                 # a real worker thread this time
    seed(ev)
    check(ev)
    download(ev)
    deadline = time.time() + 20
    state = {}
    while time.time() < deadline:
        state = ev.get("/events/1000/changes/progress").get_json()
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state["state"] == "done" and state["done"] == 5 and state["failed"] == 0 and state["total"] == 5
    assert "Downloaded 5 file(s)" in text_of(page(ev))
    assert len(history(cfg)) == 5


def test_only_one_job_runs_per_event_and_the_page_shows_progress_and_cancel(ev):
    seed(ev)
    check(ev)
    import threading
    registry = ev.application.extensions["ledsync.jobs"]
    gate = threading.Event()

    def slow(progress):
        progress.start(3)
        progress.begin("big.mp4", 1000)
        progress.add_bytes(250)
        gate.wait(10)
        return [{"level": "success", "text": "finished"}]
    job = registry.start("1000", slow)
    assert job is not None and registry.start("1000", slow) is None                   # a second one is refused
    html = page(ev)
    assert 'id="progress"' in html and "Cancel Download" in html and "js/changes.js" in html and "DOWNLOAD FILES" not in html
    assert 'action="/events/1000/changes/cancel"' in html
    snap = ev.get("/events/1000/changes/progress").get_json()
    assert snap["state"] == "running" and snap["current"] == "big.mp4" and snap["percent"] == 25 and snap["total"] == 3
    out = download(ev)
    assert "already running" in text_of(out.get_data(as_text=True))
    ev.post("/events/1000/changes/cancel", data={"csrf_token": token(ev)})
    assert job.progress.cancelled and ev.get("/events/1000/changes/progress").get_json()["cancelling"] is True
    gate.set()
    job.thread.join(5)
    assert not job.thread.is_alive() and job.progress.state == "done"
    assert registry.take_summary("1000") == [{"level": "success", "text": "finished"}]
    gate.set()                                                        # a new job can start once the first has ended
    assert registry.start("1000", slow, inline=True) is not None


def test_a_job_that_crashes_ends_in_an_error_state_not_a_stuck_one():
    registry = downloads.JobRegistry()

    def boom(progress):
        raise RuntimeError("secret detail")
    job = registry.start("1000", boom, inline=True)
    assert job.progress.state == "error" and "secret" not in str(job.summary) and not registry.running("1000")
    assert registry.take_summary("1000") == [{"level": "error", "text": "The run stopped because of an unexpected problem."}]
    assert registry.take_summary("1000") == []


def test_job_lookup_ignores_event_id_case():
    registry = downloads.JobRegistry()
    registry.start("Doha-A", lambda p: [{"level": "info", "text": "x"}], inline=True)
    assert registry.get("doha-a") is not None


def test_progress_numbers(cfg):
    p = Progress()
    p.start(4)
    p.begin("a.png", 200)
    p.add_bytes(50)
    assert p.snapshot() == {"state": "running", "phase": "", "total": 4, "done": 0, "failed": 0, "current": "a.png", "percent": 25,
                            "message": "", "cancelling": False, "identified": 0, "downloaded": 0, "synchronised": 0, "errors": 0}
    p.finish_file(False)
    p.cancel()
    snap = p.snapshot()
    assert snap["done"] == 1 and snap["failed"] == 1 and snap["cancelling"] is True and snap["current"] == ""
    p.end("cancelled", "stopped")
    assert p.snapshot()["state"] == "cancelled" and p.snapshot()["message"] == "stopped"


# --- Settings: both folders ---------------------------------------------------------------------------------------------------

def test_the_folder_settings_page_has_both_folders_and_refuses_nested_ones(logged_in, cfg, tmp_path_factory):
    html = logged_in.get("/settings/folders").get_data(as_text=True)
    assert 'name="asset_folder"' in html and 'name="rpi_folder"' in html and str(cfg.data_dir / "Events") in html
    base = tmp_path_factory.mktemp("b")
    resp = logged_in.post("/settings/folders", data={"asset_folder": str(base / "X"), "rpi_folder": str(base / "X" / "RPI"),
                                                     "csrf_token": csrf_from(logged_in, "/settings/folders")})
    assert resp.status_code == 400 and "different folders" in resp.get_data(as_text=True)
    assert str(base / "X") in resp.get_data(as_text=True)                       # what was typed is kept


def test_the_change_log_page_names_both_destination_folders(ev, cfg):
    seed(ev)
    text = text_of(check(ev).get_data(as_text=True))
    assert str(cfg.data_dir / "Events") in text and str(cfg.data_dir / "RPI") in text
