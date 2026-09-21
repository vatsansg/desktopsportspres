"""Regression tests for the independent review of Phase 7 (21/09/26)."""

import os
import subprocess
import threading
import time

import pytest
from conftest import csrf_from, db_rows, serve_event
from fakes import ACCOUNT, FAKE_KEY, FakeBlobService, http_error
from phase6_helpers import REAL_GUID_JSON, T0, T1, T2, csv_text, parsed, real_structure

from ledsync.db import connect, init_db
from ledsync.services import assets, changes, downloads, exceptions, localfiles, rpi, transfer
from ledsync.services import settings as cs
from ledsync.services.progress import Progress
from ledsync.services.storage import AzureReadOnlyStorage, EventLocation

FOLDER = "1000 - Star contender Doha"
LOC = EventLocation("2026", FOLDER)
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 20


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch):
    monkeypatch.setattr(transfer, "RETRY_DELAY", 0)


@pytest.fixture
def fake():
    f = FakeBlobService()
    f.containers.add("2026")
    return f


def storage_for(fake):
    return AzureReadOnlyStorage(ACCOUNT, FAKE_KEY, service_factory=fake.factory)


def blob(fake, path, data=PNG):
    fake.put("2026", f"{FOLDER}/{path}", data)


@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    c.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('1000', 'x', 'g')")
    c.commit()
    yield c
    c.close()


def comparison(conn, fake, rows):
    local = changes.local_state(conn, "1000")
    return changes.add_azure_files(changes.compare(parsed(rows), real_structure(), local), storage_for(fake), LOC, real_structure(), local)


def go(conn, fake, rows, root, data_dir):
    return assets.process(conn, storage_for(fake), ACCOUNT, LOC, "1000", comparison(conn, fake, rows), root, data_dir)


def tree(root):
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())


# --- 1: files that exist only in Azure, in any spelling of the folder, can actually be downloaded ------------------------

def test_an_azure_only_file_in_a_main_led_folder_is_downloaded(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/Main LED/a.png", b"main led file")
    result = go(conn, fake, [], root, cfg.data_dir)
    assert result.downloaded == 1 and result.failed == 0
    assert (root / "1000" / "Table 1" / "Main LED" / "a.png").read_bytes() == b"main led file"
    assert go(conn, fake, [], root, cfg.data_dir).total == 0                       # and it is not offered again


def test_an_azure_only_file_in_a_lower_case_mainled_folder_is_downloaded(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/mainled/a.png", b"x")
    assert go(conn, fake, [], root, cfg.data_dir).downloaded == 1


def test_the_same_name_in_two_azure_folders_is_reported_not_silently_dropped(conn, fake):
    blob(fake, "Table 1/mainled/a.png", b"one")
    blob(fake, "Table 1/Main LED/a.png", b"two")
    blob(fake, "Table 1/mainled/other.png", b"three")
    cmp = comparison(conn, fake, [])
    assert [a.file_name for a in cmp.assessments] == ["other.png"]
    assert "clash" in cmp.azure_note


@pytest.mark.parametrize("one,two", [("a.png", "A.png"), ("Straße.png", "Strasse.png"), ("é.png", "é.png")])
def test_azure_names_that_would_be_one_local_file_are_not_offered_and_are_reported(conn, fake, one, two):
    blob(fake, "Table 2/inner/" + one)
    blob(fake, "Table 2/inner/" + two)
    blob(fake, "Table 2/inner/fine.png")
    cmp = comparison(conn, fake, [])
    assert [a.file_name for a in cmp.assessments] == ["fine.png"] and "clash" in cmp.azure_note


def test_unsafe_azure_names_are_left_out_but_the_operator_is_told(conn, fake):
    for name in ("NUL.png", "setup.exe", "a.png:s", "LONGFI~1.PNG"):
        blob(fake, f"Table 2/inner/{name}")
    blob(fake, "Table 2/inner/keepalive.txt")
    blob(fake, "Table 2/inner/ok.png")
    cmp = comparison(conn, fake, [])
    assert [a.file_name for a in cmp.assessments] == ["ok.png"]
    assert "4 file(s) in Azure were ignored" in cmp.azure_note                       # placeholders are not counted


def test_a_placeholder_named_in_the_change_log_is_not_an_asset_either():
    a = changes.compare(parsed([("Table 1/Inner/keepalive.txt", T0, "New")]), real_structure(), {}).assessments[0]
    assert a.action == changes.NOT_APPLICABLE and "placeholder" in a.reason


def test_thousands_of_unlogged_files_are_compared_quickly(conn, fake):
    for i in range(3000):
        blob(fake, f"Table 2/inner/f{i:05d}.png", b"x")
    started = time.monotonic()
    cmp = comparison(conn, fake, [])
    assert len(cmp.assessments) == 3000 and time.monotonic() - started < 6


def test_an_azure_file_with_no_last_modified_still_gets_a_valid_history_time(conn, fake, cfg, tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 2/inner/a.png")
    fake.modified = None                                                              # Azure listed no time
    assert go(conn, fake, [], root, cfg.data_dir).downloaded == 1
    [row] = db_rows(cfg, "SELECT source_timestamp FROM download_history")
    assert row["source_timestamp"].endswith("Z") and go(conn, fake, [], root, cfg.data_dir).total == 0


# --- 2: a removal can never follow a junction -----------------------------------------------------------------------------

def test_existing_subfolder_is_none_for_a_missing_folder_and_refuses_a_link(tmp_path_factory):
    root, outside = tmp_path_factory.mktemp("r"), tmp_path_factory.mktemp("o")
    assert localfiles.existing_subfolder(root, "1000", "Table 1") is None
    (root / "1000").mkdir()
    assert localfiles.existing_subfolder(root, "1000") == root / "1000"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "1000" / "Table 1"), str(outside)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("could not create a junction here")
    with pytest.raises(localfiles.LocalFileError):
        localfiles.existing_subfolder(root, "1000", "Table 1")


def test_a_cloud_removal_never_deletes_through_a_junction_at_table_or_led_level(conn, fake, cfg, tmp_path_factory):
    root, outside = tmp_path_factory.mktemp("assets"), tmp_path_factory.mktemp("outside")
    (outside / "a.png").write_bytes(b"victim")
    (root / "1000" / "Table 1").mkdir(parents=True)
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "1000" / "Table 1" / "Inner"), str(outside)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("could not create a junction here")
    local = {changes.path_key(1, "Inner", "a.png"): changes.LocalRecord("Success", parsed([("Table 1/Inner/x.png", T0, "New")]).entries[0].timestamp)}
    cmp = changes.compare(parsed([("Table 1/Inner/a.png", T1, "Deleted")]), real_structure(), local)
    result = assets.process(conn, storage_for(fake), ACCOUNT, LOC, "1000", cmp, root, cfg.data_dir)
    assert result.removed == 0 and result.failed == 1
    assert (outside / "a.png").read_bytes() == b"victim"


# --- 3: temporary files left by a crash are cleaned up where they are made ---------------------------------------------------

def test_a_stale_temporary_file_in_a_table_folder_is_swept_when_that_folder_is_used(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    folder = root / "1000" / "Table 1" / "Inner"
    folder.mkdir(parents=True)
    stale, fresh, users = (folder / ("." + "a" * 32 + ".ledsync-tmp"), folder / ("." + "b" * 32 + ".ledsync-tmp"),
                           folder / "mine.ledsync-tmp")
    for f in (stale, fresh, users):
        f.write_bytes(b"x")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    os.utime(users, (old, old))
    localfiles._swept.clear()
    blob(fake, "Table 1/inner/a.png")
    assert go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], root, cfg.data_dir).downloaded == 1
    assert sorted(p.name for p in folder.iterdir()) == sorted(["a.png", fresh.name, users.name])


def test_a_stale_temporary_file_in_an_rpi_event_folder_is_swept(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("rpi")
    (root / "1000").mkdir()
    stale = root / "1000" / ("." + "c" * 32 + ".ledsync-tmp")
    stale.write_bytes(b"x")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    localfiles._swept.clear()
    blob(fake, "rpi/a.png")
    cmp = changes.compare(parsed([("RPI/a.png", T0, "New")]), real_structure(), {})
    rpi.process(conn, storage_for(fake), ACCOUNT, LOC, "1000", cmp, root, cfg.data_dir)
    assert sorted(p.name for p in (root / "1000").iterdir()) == ["a.png"]


# --- 4: a finished job's summary is never lost ------------------------------------------------------------------------------

def test_the_summary_is_in_place_before_the_job_says_it_has_finished(monkeypatch):
    original = Progress.end

    def slow_end(self, state, message):                     # widen the old race window
        time.sleep(0.05)
        original(self, state, message)
    monkeypatch.setattr(Progress, "end", slow_end)
    registry = downloads.JobRegistry()

    def work(progress):
        time.sleep(0.05)
        return [{"level": "success", "text": "done"}]
    job = registry.start("1000", work)
    seen = None
    deadline = time.time() + 10
    while time.time() < deadline:
        if job.progress.state != "running":
            seen = registry.take_summary("1000")
            break
        registry.take_summary("1000")                        # polling while it runs must never consume the summary
        time.sleep(0.005)
    assert seen == [{"level": "success", "text": "done"}]


# --- 5: a file that is gone from disk AND from Azure stops being offered -------------------------------------------------------

def test_a_downloaded_file_that_vanished_from_both_places_is_dropped_from_the_list_not_failed_forever(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/a.png")
    rows = [("Table 1/Inner/a.png", T0, "New")]
    go(conn, fake, rows, root, cfg.data_dir)
    (root / "1000" / "Table 1" / "Inner" / "a.png").unlink()
    del fake.blobs[("2026", f"{FOLDER}/Table 1/inner/a.png")]
    result = go(conn, fake, rows, root, cfg.data_dir)
    assert result.gone == 1 and result.failed == 0 and result.downloaded == 0
    assert [h["status"] for h in db_rows(cfg, "SELECT status FROM download_history ORDER BY download_id")] == ["Success", "Deleted"]
    assert db_rows(cfg, "SELECT COUNT(*) AS n FROM exception_log")[0]["n"] == 0
    again = go(conn, fake, rows, root, cfg.data_dir)
    assert again.total == 0 and len(db_rows(cfg, "SELECT * FROM download_history")) == 2


def test_a_new_file_whose_blob_is_missing_is_still_a_failure_to_retry(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    result = go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], root, cfg.data_dir)
    assert result.failed == 1 and result.gone == 0


# --- 6, 7: a retry sees Azure as it is now, and a replaced file can never be mixed --------------------------------------------

def replace_on_first_download(fake, path, new_data):
    original, state = fake._check, {"done": False}

    def hook(what):
        if what == "download" and not state["done"]:
            state["done"] = True
            blob(fake, path, new_data)                     # the file is replaced in Azure while it is being downloaded
        return original(what)
    fake._check = hook
    return state


def test_a_file_replaced_in_azure_during_the_download_is_detected_and_the_retry_gets_the_new_version(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/a.png", b"version one")
    state = replace_on_first_download(fake, "Table 1/inner/a.png", b"version TWO!!")
    result = go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], root, cfg.data_dir)
    assert state["done"] and result.downloaded == 1 and result.failed == 0
    assert (root / "1000" / "Table 1" / "Inner" / "a.png").read_bytes() == b"version TWO!!"


def test_the_same_holds_when_azure_lists_no_fingerprint_and_the_size_is_unchanged(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    fake.no_md5 = True
    blob(fake, "Table 1/inner/a.png", b"AAAAAAAA")
    replace_on_first_download(fake, "Table 1/inner/a.png", b"BBBBBBBB")                # same size, no MD5 to catch it
    assert go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], root, cfg.data_dir).downloaded == 1
    assert (root / "1000" / "Table 1" / "Inner" / "a.png").read_bytes() == b"BBBBBBBB"


def test_a_file_that_keeps_changing_fails_plainly_and_leaves_the_old_copy(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/a.png", b"old good")
    go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], root, cfg.data_dir)
    blob(fake, "Table 1/inner/a.png", b"first change")
    original, counter = fake._check, {"n": 0}

    def always_changing(what):
        if what == "download":
            counter["n"] += 1
            blob(fake, "Table 1/inner/a.png", f"change {counter['n']}".encode())
        return original(what)
    fake._check = always_changing
    result = go(conn, fake, [("Table 1/Inner/a.png", T0, "New"), ("Table 1/Inner/a.png", T1, "Updated")], root, cfg.data_dir)
    assert result.failed == 1 and "changed in Azure" in result.failures[0]
    assert (root / "1000" / "Table 1" / "Inner" / "a.png").read_bytes() == b"old good"


# --- 11, 12: Azure being busy also ends the run; stopping and cancelling skip the slow tidy-up ------------------------------------

@pytest.mark.parametrize("status", [429, 500, 503])
def test_azure_being_busy_stops_the_run_like_a_lost_connection(conn, fake, cfg, tmp_path_factory, monkeypatch, status):
    root = tmp_path_factory.mktemp("assets")
    for n in "abc":
        blob(fake, f"Table 1/inner/{n}.png")

    def busy(what):
        fake.calls.append(what)
        raise http_error(status)
    monkeypatch.setattr(fake, "_check", busy)
    result = go(conn, fake, [(f"Table 1/Inner/{n}.png", T0, "New") for n in "abc"], root, cfg.data_dir)
    assert result.stopped and result.failed == 1 and result.remaining == 2
    assert exceptions.STORAGE_CONNECTIVITY in [r["category"] for r in db_rows(cfg, "SELECT category FROM exception_log")]


@pytest.fixture
def ev(logged_in):
    serve_event(logged_in.application.extensions["test.azure"], dict(REAL_GUID_JSON))
    logged_in.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(logged_in, "/events/new")})
    return logged_in


def put(client, path, data=PNG):
    client.application.extensions["test.azure"].put("2026", f"{FOLDER}/{path}", data)


def seed(ev):
    put(ev, "rpi/HOME_Look.png")
    for n in "abcd":
        put(ev, f"Table 1/inner/{n}.png")
    put(ev, "_ledassetschangelog.csv", csv_text([("RPI/HOME_Look.png", T1, "New")] + [(f"Table 1/Inner/{n}.png", T1, "New") for n in "abcd"]))


def run_job(ev, cfg, progress):
    app = ev.application
    conn = connect(cfg.db_path)
    settings = cs.load_cloud(conn)
    conn.close()
    return downloads.run_download(app.config["LEDSYNC"], app.extensions["ledsync.storage_factory"], settings, "1000", progress)


def test_when_the_run_stops_the_files_not_tried_include_the_other_engine_and_nothing_slow_follows(ev, cfg, monkeypatch):
    seed(ev)
    monkeypatch.setattr(rpi, "process", lambda *a, **k: transfer.TransferResult(failed=1, stopped=True, failures=["x: lost"]))
    called = []
    monkeypatch.setattr(assets, "process", lambda *a, **k: called.append(1))
    calls_before = len(ev.application.extensions["test.azure"].calls)
    progress = Progress()
    lines = run_job(ev, cfg, progress)
    assert called == [] and progress.final_state == "done"
    text = " ".join(line["text"] for line in lines)
    assert "4 file(s) were not tried" in text and lines[0]["level"] == "error"
    assert len(ev.application.extensions["test.azure"].calls) - calls_before < 25          # no second full check after a stop


def test_a_cancelled_job_ends_at_once_and_reports_every_file_it_did_not_do(ev, cfg):
    seed(ev)
    progress = Progress()
    progress.cancel()
    lines = run_job(ev, cfg, progress)
    assert progress.final_state == "cancelled" and lines[0]["level"] == "info"
    assert "Cancelled; 5 file(s) were not downloaded" in " ".join(line["text"] for line in lines)
    assert not (cfg.data_dir / "Events").exists() or not any((cfg.data_dir / "Events").rglob("*.png"))


def test_a_download_with_problems_is_styled_as_an_error_and_a_clean_one_as_success(ev, cfg):
    seed(ev)
    put(ev, "_ledassetschangelog.csv", csv_text([("Table 1/Inner/a.png", T1, "New"), ("Table 1/Inner/ghost.png", T1, "New")]))
    tok = lambda: csrf_from(ev, "/events/1000/changes")           # noqa: E731
    ev.post("/events/1000/changes/check", data={"csrf_token": tok()})
    html = ev.post("/events/1000/changes/download", data={"csrf_token": tok()}, follow_redirects=True).get_data(as_text=True)
    assert 'class="alert alert-error"' in html and "ghost.png" in html
    ev.post("/events/1000/changes/check", data={"csrf_token": tok()})
    html = ev.post("/events/1000/changes/download", data={"csrf_token": tok()}, follow_redirects=True).get_data(as_text=True)
    assert "alert-success" not in html or "failed 0." not in html                          # the ghost still fails: still an error


# --- the page while running -----------------------------------------------------------------------------------------------------

def test_the_progress_panel_has_a_no_script_fallback_and_a_text_value_for_the_bar(ev):
    seed(ev)
    registry = ev.application.extensions["ledsync.jobs"]
    gate = threading.Event()
    job = registry.start("1000", lambda p: (p.start(1), gate.wait(10), [{"level": "success", "text": "ok"}])[2])
    try:
        html = ev.get("/events/1000/changes").get_data(as_text=True)
        assert "<noscript>" in html and "Reload" in html and 'id="progress-bar"' in html
        js = ev.get("/static/js/changes.js").get_data(as_text=True)
        assert "aria-valuetext" in js and "last" in js
    finally:
        gate.set()
        job.thread.join(5)


# --- settings: folders judged by identity ---------------------------------------------------------------------------------------

@pytest.fixture
def sconn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    yield c
    c.close()


def test_a_junction_alias_cannot_hide_that_one_download_folder_is_inside_the_other(sconn, cfg, tmp_path_factory):
    base = tmp_path_factory.mktemp("b")
    (base / "A" / "sub").mkdir(parents=True)
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(base / "J"), str(base / "A" / "sub")], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("could not create a junction here")
    with pytest.raises(cs.SettingsError) as err:
        cs.save_folders(sconn, str(base / "A"), str(base / "J"), cfg.data_dir)
    assert "different folders" in str(err.value)


def test_a_short_name_alias_of_the_same_folder_is_seen_as_the_same_folder(sconn, cfg, tmp_path_factory):
    import ctypes
    base = tmp_path_factory.mktemp("Some Long Base Name")
    (base / "Assets Folder").mkdir()
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.kernel32.GetShortPathNameW(str(base / "Assets Folder"), buf, 512)
    if not buf.value or buf.value.casefold() == str(base / "Assets Folder").casefold():
        pytest.skip("no 8.3 names on this machine")
    with pytest.raises(cs.SettingsError):
        cs.save_folders(sconn, str(base / "Assets Folder"), buf.value, cfg.data_dir)


def test_a_download_folder_that_contains_the_data_folder_is_refused_when_saved(sconn, cfg, tmp_path_factory):
    with pytest.raises(cs.SettingsError) as err:
        cs.save_folders(sconn, "", str(cfg.data_dir.parent), cfg.data_dir)
    assert "cannot contain" in str(err.value)
    with pytest.raises(cs.SettingsError):
        cs.save_folders(sconn, str(cfg.data_dir.parent.parent), "", cfg.data_dir)


# --- lengths, hashing, page bounds ----------------------------------------------------------------------------------------------

def test_a_path_that_would_be_too_long_says_so_instead_of_blaming_the_disk(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    deep = root
    for _ in range(4):
        deep = deep / ("d" * 50)
    with pytest.raises(localfiles.LocalFileError) as err:
        localfiles.open_subfolder(deep, "1000", "Table 1", "Inner")
    assert "too long" in str(err.value)
    with pytest.raises(localfiles.LocalFileError) as err:
        localfiles.write_atomic(root, "a" * 250 + ".png", b"x")
    assert "too long" in str(err.value) or "cannot be used" in str(err.value)


def test_checksums_are_computed_without_claiming_security_use(tmp_path_factory, monkeypatch):
    import hashlib
    real = hashlib.md5

    def strict(*args, usedforsecurity=True):
        if usedforsecurity:
            raise ValueError("disabled in FIPS mode")
        return real(*args, usedforsecurity=False)
    monkeypatch.setattr(localfiles.hashlib, "md5", strict)
    root = tmp_path_factory.mktemp("w")
    assert localfiles.write_stream(root, "a.png", [b"abc"], size=3, md5=real(b"abc").digest()).read_bytes() == b"abc"


def test_the_page_disk_checks_are_bounded_and_never_raise(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/a.png")
    rows = [("Table 1/Inner/a.png", T0, "New")]
    go(conn, fake, rows, root, cfg.data_dir)
    (root / "1000" / "Table 1" / "Inner" / "a.png").unlink()
    cmp = comparison(conn, fake, rows)
    assert [i.file_name for i in assets.asset_items(cmp, root / "1000", budget=8.0)] == ["a.png"]
    started = time.monotonic()
    assert assets.asset_items(cmp, root / "1000", budget=0.0) == []                       # out of time: shows only what is certain
    assert time.monotonic() - started < 1


def test_a_server_error_is_no_longer_reported_as_a_download_problem():
    from ledsync.services.storage import map_error
    assert map_error(http_error(503), "x").category == exceptions.STORAGE_CONNECTIVITY
    assert map_error(http_error(429), "x").category == exceptions.STORAGE_CONNECTIVITY
    assert map_error(http_error(400), "x").category == exceptions.DOWNLOAD
