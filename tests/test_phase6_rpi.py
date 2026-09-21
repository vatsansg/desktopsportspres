"""Phase 6 addition - RPI files: real blob-name lookup, the RPI folder setting, safe local writes, and the
download of the event's `RPI/<file>` entries (owner decision 21/09/26)."""

import csv
import os
import re
import subprocess

import pytest
from conftest import csrf_from, db_rows, serve_event
from fakes import ACCOUNT, FAKE_KEY, FakeBlobService
from phase6_helpers import REAL_GUID_JSON, T0, T1, T2, csv_text

from ledsync.db import connect, init_db
from ledsync.services import exceptions, localfiles, rpi
from ledsync.services import settings as cs
from ledsync.services.storage import AzureReadOnlyStorage, EventLocation, StorageError

FOLDER = "1000 - Star contender Doha"
LOC = EventLocation("2026", FOLDER)
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 10


# --- finding the real blob (Azure names are case-sensitive; the log says `RPI`, Azure holds `rpi`) --------------------

@pytest.fixture
def fake():
    f = FakeBlobService()
    f.containers.add("2026")
    return f


def storage_for(fake):
    return AzureReadOnlyStorage(ACCOUNT, FAKE_KEY, service_factory=fake.factory)


def blob(fake, path, data=b"x"):
    fake.put("2026", f"{FOLDER}/{path}", data)


@pytest.mark.parametrize("asked,held", [("RPI/HOME_Look.png", "rpi/HOME_Look.png"), ("rpi/home_look.PNG", "rpi/HOME_Look.png"),
                                        ("RPI/HOME_Look.png", "RPI/HOME_Look.png"), ("Table 1/Inner/a.png", "Table 1/inner/a.png")])
def test_the_real_blob_name_is_found_whatever_the_letter_case(fake, asked, held):
    blob(fake, held)
    assert storage_for(fake).resolve_relative_path(LOC, asked) == held


def test_a_missing_file_says_so(fake):
    blob(fake, "rpi/other.png")
    with pytest.raises(StorageError) as err:
        storage_for(fake).resolve_relative_path(LOC, "RPI/HOME_Look.png")
    assert err.value.category == exceptions.MISSING_FOLDER and "was not found" in err.value.message


def test_two_names_that_differ_only_in_case_are_ambiguous_not_guessed(fake):
    blob(fake, "rpi/a.png")
    blob(fake, "rpi/A.PNG")
    with pytest.raises(StorageError) as err:
        storage_for(fake).resolve_relative_path(LOC, "RPI/a.png")
    assert err.value.category == exceptions.CONFIGURATION and "More than one" in err.value.message


def test_only_ascii_letter_case_is_ignored(fake):
    blob(fake, "rpi/Stra\u00dfe.png")
    with pytest.raises(StorageError):
        storage_for(fake).resolve_relative_path(LOC, "RPI/STRASSE.png")
    assert storage_for(fake).resolve_relative_path(LOC, "rpi/STRA\u00dfE.png") == "rpi/Stra\u00dfe.png"


def test_a_folder_is_not_a_file_and_a_file_is_not_a_folder(fake):
    blob(fake, "rpi/x.png")
    with pytest.raises(StorageError):
        storage_for(fake).resolve_relative_path(LOC, "RPI")
    with pytest.raises(StorageError):
        storage_for(fake).resolve_relative_path(LOC, "RPI/x.png/y.png")


def test_unsafe_paths_are_refused_before_any_listing(fake):
    with pytest.raises(StorageError):
        storage_for(fake).resolve_relative_path(LOC, "../secret.txt")
    assert fake.calls == []


def test_looking_up_a_name_only_reads(fake):
    blob(fake, "rpi/x.png")
    storage_for(fake).resolve_relative_path(LOC, "RPI/x.png")
    assert set(fake.calls) <= {"list"}


# --- the safe local writer ---------------------------------------------------------------------------------------------

def test_a_file_is_written_atomically_and_replaces_an_older_copy(tmp_path):
    localfiles.write_atomic(tmp_path, "a.png", b"old")
    target = localfiles.write_atomic(tmp_path, "a.png", b"new")
    assert target.read_bytes() == b"new" and sorted(p.name for p in tmp_path.iterdir()) == ["a.png"]


@pytest.mark.parametrize("name", ["..", ".", "a/b.png", "sub\\a.png", "NUL", "nul.png", "a.png:s", "a.png.", "a.png ", "",
                                  "con.txt", "a<b>.png", "x" * 300, "C:\\x.png"], ids=lambda v: ascii(v)[:16])
def test_names_that_are_not_plain_safe_file_names_are_refused(tmp_path, name):
    with pytest.raises(localfiles.LocalFileError):
        localfiles.write_atomic(tmp_path, name, b"x")
    with pytest.raises(localfiles.LocalFileError):
        localfiles.delete_file(tmp_path, name)
    assert list(tmp_path.iterdir()) == []


def test_a_folder_with_the_file_name_is_never_replaced_or_deleted(tmp_path):
    (tmp_path / "a.png").mkdir()
    (tmp_path / "a.png" / "keep.txt").write_text("keep")
    with pytest.raises(localfiles.LocalFileError):
        localfiles.write_atomic(tmp_path, "a.png", b"x")
    with pytest.raises(localfiles.LocalFileError):
        localfiles.delete_file(tmp_path, "a.png")
    assert (tmp_path / "a.png" / "keep.txt").read_text() == "keep" and not list(tmp_path.glob("*.tmp"))


def test_a_junction_with_the_file_name_is_never_followed(tmp_path_factory):
    root, outside = tmp_path_factory.mktemp("root"), tmp_path_factory.mktemp("outside")
    (outside / "victim.txt").write_text("victim")
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "a.png"), str(outside)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("could not create a junction on this machine")
    with pytest.raises(localfiles.LocalFileError):
        localfiles.write_atomic(root, "a.png", b"x")
    with pytest.raises(localfiles.LocalFileError):
        localfiles.delete_file(root, "a.png")
    assert sorted(p.name for p in outside.iterdir()) == ["victim.txt"]


def test_a_failed_write_leaves_no_temporary_file_and_no_half_file(tmp_path, monkeypatch):
    def boom(src, dst):
        raise PermissionError(5, "denied")
    monkeypatch.setattr(localfiles.os, "replace", boom)
    with pytest.raises(localfiles.LocalFileError) as err:
        localfiles.write_atomic(tmp_path, "a.png", b"x")
    assert "could not be replaced" in str(err.value) and list(tmp_path.iterdir()) == []


def test_delete_removes_only_that_file_and_reports_a_missing_one(tmp_path):
    (tmp_path / "a.png").write_bytes(b"1")
    (tmp_path / "b.png").write_bytes(b"2")
    assert localfiles.delete_file(tmp_path, "a.png") is True
    assert localfiles.delete_file(tmp_path, "a.png") is False
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.png"]


def test_the_folder_is_created_when_missing_and_dangerous_folders_are_refused(cfg, tmp_path_factory):
    good = tmp_path_factory.mktemp("g") / "deep" / "RPI"
    assert localfiles.open_root(good, cfg.data_dir).is_dir()
    assert localfiles.open_root(cfg.data_dir / "RPI", cfg.data_dir).is_dir()          # the default lives inside the data folder
    for bad in (cfg.data_dir, os.environ["SystemRoot"], os.path.join(os.environ["SystemRoot"], "System32")):
        with pytest.raises(localfiles.LocalFileError):
            localfiles.open_root(bad, cfg.data_dir)


def test_a_folder_that_cannot_be_created_is_a_plain_error(tmp_path, cfg):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    with pytest.raises(localfiles.LocalFileError) as err:
        localfiles.open_root(blocker / "RPI", cfg.data_dir)
    assert "could not be created" in str(err.value)


# --- the setting ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    yield c
    c.close()


def test_the_default_rpi_folder_is_inside_the_application_data_folder(conn, cfg):
    loaded = cs.load_rpi_folder(conn, cfg.data_dir)
    assert loaded.is_default and loaded.saved == "" and loaded.effective == cfg.data_dir / "RPI"


def test_a_chosen_folder_is_saved_and_blank_returns_to_the_default(conn, cfg, tmp_path_factory):
    mine = str(tmp_path_factory.mktemp("mine") / "RPI")
    assert cs.save_rpi_folder(conn, mine, cfg.data_dir) is True
    loaded = cs.load_rpi_folder(conn, cfg.data_dir)
    assert not loaded.is_default and str(loaded.effective) == mine
    assert cs.save_rpi_folder(conn, mine, cfg.data_dir) is False                       # same value: no change
    assert cs.save_rpi_folder(conn, "  ", cfg.data_dir) is True
    assert cs.load_rpi_folder(conn, cfg.data_dir).is_default


@pytest.mark.parametrize("bad", ["RPI", "..\\x", "C:\\Windows\\RPI", "C:\\", "\\\\?\\C:\\x", "C:\\a:b", "http://x/y", "C:\\ok\\NUL"],
                         ids=lambda v: v[:14])
def test_unsafe_rpi_folders_are_refused_and_nothing_is_saved(conn, cfg, bad):
    with pytest.raises(cs.SettingsError) as err:
        cs.save_rpi_folder(conn, bad, cfg.data_dir)
    assert "default RPI folder" in str(err.value)
    assert cs.load_rpi_folder(conn, cfg.data_dir).is_default


def test_a_folder_inside_the_data_folder_is_refused(conn, cfg):
    with pytest.raises(cs.SettingsError):
        cs.save_rpi_folder(conn, str(cfg.data_dir / "elsewhere"), cfg.data_dir)


def test_saving_is_audited_and_the_setting_stays_in_its_own_lane(conn, cfg, tmp_path_factory):
    cs.save_rpi_folder(conn, str(tmp_path_factory.mktemp("m") / "RPI"), cfg.data_dir)
    log = db_rows(cfg, "SELECT message FROM operation_log WHERE operation = 'Settings Changed'")
    assert log == [{"message": "Local folders saved (RPI folder changed)."}]
    with pytest.raises(PermissionError):
        cs._get(conn, "admin_password")


# --- the Settings page -----------------------------------------------------------------------------------------------------

def test_the_settings_pages_are_gated_and_share_a_navigation(app, client, launched, logged_in):
    assert client.get("/settings/folders").status_code == 403
    for path in ("/settings/cloud", "/settings/folders"):
        html = logged_in.get(path).get_data(as_text=True)
        assert 'href="/settings/cloud"' in html and 'href="/settings/folders"' in html and 'aria-current="page"' in html


def test_the_folder_page_shows_the_default_and_saves_a_choice(logged_in, cfg, tmp_path_factory):
    html = logged_in.get("/settings/folders").get_data(as_text=True)
    assert str(cfg.data_dir / "RPI") in html and "Default folder" in html and html.count("btn-primary") == 1
    mine = str(tmp_path_factory.mktemp("mine") / "RPI")
    resp = logged_in.post("/settings/folders", data={"rpi_folder": mine, "csrf_token": csrf_from(logged_in, "/settings/folders")},
                          follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "Local folder settings saved." in html and mine in html and "Your folder" in html


def test_a_bad_folder_is_explained_and_kept_in_the_box(logged_in):
    resp = logged_in.post("/settings/folders", data={"rpi_folder": "relative\\RPI", "csrf_token": csrf_from(logged_in, "/settings/folders")})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "full folder path" in html and "relative\\RPI" in html and 'role="alert"' in html


def test_the_folder_page_refuses_a_post_without_csrf(logged_in, cfg):
    assert logged_in.post("/settings/folders", data={"rpi_folder": "C:\\x"}).status_code == 403


# --- the whole flow through the Change Log page ----------------------------------------------------------------------------------

@pytest.fixture
def ev(logged_in):
    serve_event(logged_in.application.extensions["test.azure"], dict(REAL_GUID_JSON))
    logged_in.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(logged_in, "/events/new")})
    return logged_in


def azure_of(client):
    return client.application.extensions["test.azure"]


def put(client, path, data):
    azure_of(client).put("2026", f"{FOLDER}/{path}", data)


def put_log(client, rows):
    put(client, "_ledassetschangelog.csv", csv_text(rows))


def page(client):
    return client.get("/events/1000/changes").get_data(as_text=True)


def check(client):
    return client.post("/events/1000/changes/check", data={"csrf_token": csrf_from(client, "/events/1000/changes")},
                       follow_redirects=True)


def download(client):
    return client.post("/events/1000/changes/download", data={"csrf_token": csrf_from(client, "/events/1000/changes")},
                       follow_redirects=True)


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def history(cfg):
    return db_rows(cfg, "SELECT event_id, file_name, table_number, led_type, source_timestamp, status, local_path FROM download_history")


def test_the_rpi_file_is_downloaded_into_the_default_rpi_folder(ev, cfg):
    put(ev, "rpi/HOME_Look.png", PNG)                                   # Azure holds lower-case `rpi`, the log says `RPI`
    put_log(ev, [("RPI/HOME_Look.png", T1, "New")])
    check(ev)
    html = page(ev)
    assert "DOWNLOAD FILES (1)" in html and str(cfg.data_dir / "RPI") in html
    assert html.count("btn-primary") == 1                               # still only one orange action on the page
    out = download(ev).get_data(as_text=True)
    assert "Downloaded 1 file(s), removed 0, failed 0." in out
    assert (cfg.data_dir / "RPI" / "1000" / "HOME_Look.png").read_bytes() == PNG
    assert sorted(p.name for p in (cfg.data_dir / "RPI" / "1000").iterdir()) == ["HOME_Look.png"]
    [row] = history(cfg)
    assert row["event_id"] == "1000" and row["file_name"] == "HOME_Look.png" and row["table_number"] is None
    assert row["led_type"] == "RPI" and row["status"] == "Success" and row["source_timestamp"] == T1
    assert row["local_path"] == str(cfg.data_dir / "RPI" / "1000" / "HOME_Look.png")
    assert "Nothing is waiting to be downloaded." in page(ev) and "Waiting To Be Processed (0)" in text_of(page(ev))
    assert db_rows(cfg, "SELECT last_download FROM events")[0]["last_download"]
    assert db_rows(cfg, "SELECT status FROM operation_log WHERE operation = 'RPI Files'") == [{"status": "Success"}]


def test_the_local_change_log_file_records_the_rpi_download(ev, cfg):
    put(ev, "rpi/HOME_Look.png", PNG)
    put_log(ev, [("RPI/HOME_Look.png", T1, "New")])
    check(ev)
    download(ev)
    rows = list(csv.reader(open(cfg.data_dir / "_localchangelog.csv", encoding="utf-8-sig", newline="")))
    assert len(rows) == 2
    one = dict(zip(rows[0], rows[1]))
    assert (one["Event ID"], one["Table"], one["LED Type"], one["File Name"], one["Download Status"]) == (
        "1000", "", "RPI", "HOME_Look.png", "Success")


def test_a_second_press_has_nothing_to_do(ev, cfg):
    put(ev, "rpi/HOME_Look.png", PNG)
    put_log(ev, [("RPI/HOME_Look.png", T1, "New")])
    check(ev)
    download(ev)
    calls = len(azure_of(ev).downloads)
    again = download(ev).get_data(as_text=True)
    assert "Nothing is waiting" in again and len(history(cfg)) == 1
    assert len([d for d in azure_of(ev).downloads if d[1].endswith("HOME_Look.png")]) == 1 and len(azure_of(ev).downloads) >= calls


def test_an_updated_file_is_replaced_and_a_deleted_one_is_removed_then_restored(ev, cfg):
    put(ev, "rpi/HOME_Look.png", PNG)
    put_log(ev, [("RPI/HOME_Look.png", T0, "New")])
    check(ev)
    download(ev)
    put(ev, "rpi/HOME_Look.png", b"second version")
    put_log(ev, [("RPI/HOME_Look.png", T0, "New"), ("RPI/HOME_Look.png", T1, "Updated")])
    assert "Updated 1" in text_of(check(ev).get_data(as_text=True))
    download(ev)
    assert (cfg.data_dir / "RPI" / "1000" / "HOME_Look.png").read_bytes() == b"second version"
    put_log(ev, [("RPI/HOME_Look.png", T0, "New"), ("RPI/HOME_Look.png", T1, "Updated"), ("RPI/HOME_Look.png", T2, "Deleted")])
    check(ev)
    out = download(ev).get_data(as_text=True)
    assert "Downloaded 0 file(s), removed 1, failed 0" in out and not (cfg.data_dir / "RPI" / "1000" / "HOME_Look.png").exists()
    assert [h["status"] for h in history(cfg)] == ["Success", "Success", "Deleted"]
    put_log(ev, [("RPI/HOME_Look.png", T0, "New"), ("RPI/HOME_Look.png", T2, "Deleted"), ("RPI/HOME_Look.png", "2026-09-17T10:00:00Z", "New")])
    check(ev)
    assert "Downloaded 1 file(s)" in download(ev).get_data(as_text=True) and (cfg.data_dir / "RPI" / "1000" / "HOME_Look.png").exists()


def test_the_chosen_folder_from_settings_is_used(ev, cfg, tmp_path_factory):
    mine = tmp_path_factory.mktemp("mine") / "RPI"
    ev.post("/settings/folders", data={"rpi_folder": str(mine), "csrf_token": csrf_from(ev, "/settings/folders")})
    put(ev, "rpi/HOME_Look.png", PNG)
    put_log(ev, [("RPI/HOME_Look.png", T1, "New")])
    check(ev)
    download(ev)
    assert (mine / "1000" / "HOME_Look.png").read_bytes() == PNG and not (cfg.data_dir / "RPI").exists()


def test_one_bad_file_never_stops_the_others_and_is_tried_again_next_time(ev, cfg):
    put(ev, "rpi/good.png", PNG)                                          # 'missing.png' has a log entry but no blob
    put_log(ev, [("RPI/good.png", T1, "New"), ("RPI/missing.png", T1, "New")])
    check(ev)
    out = download(ev).get_data(as_text=True)
    assert "Downloaded 1 file(s), removed 0, failed 1" in out and "missing.png:" in out and "was not found" in out
    assert (cfg.data_dir / "RPI" / "1000" / "good.png").exists() and not (cfg.data_dir / "RPI" / "1000" / "missing.png").exists()
    assert sorted(h["status"] for h in history(cfg)) == ["Failure", "Success"]
    ex = db_rows(cfg, "SELECT category, operation, file_name FROM exception_log")
    assert ex == [{"category": "Missing folder", "operation": "RPI Files", "file_name": "missing.png"}]
    assert "DOWNLOAD FILES (1)" in page(ev)                           # the failure did not count as processed
    put(ev, "rpi/missing.png", PNG)
    assert "Downloaded 1 file(s), removed 0, failed 0" in download(ev).get_data(as_text=True)


def test_a_file_with_no_extension_or_in_a_sub_folder_is_not_an_rpi_download(ev, cfg):
    put(ev, "rpi/README", b"x")
    put_log(ev, [("RPI/README", T1, "New"), ("RPI/sub/x.png", T1, "New")])
    text = text_of(check(ev).get_data(as_text=True))
    assert "Not Applicable To This Event (2)" in text and "Nothing is waiting to be downloaded." in page(ev)


def test_an_unusable_folder_is_a_plain_message_and_nothing_is_written(ev, cfg, tmp_path_factory):
    blocker = tmp_path_factory.mktemp("b") / "file.txt"
    blocker.write_text("x")
    ev.post("/settings/folders", data={"rpi_folder": str(blocker / "RPI"), "csrf_token": csrf_from(ev, "/settings/folders")})
    put(ev, "rpi/HOME_Look.png", PNG)
    put_log(ev, [("RPI/HOME_Look.png", T1, "New")])
    check(ev)
    out = download(ev).get_data(as_text=True)
    assert "could not be created" in out and history(cfg) == []


def test_a_hostile_rpi_name_never_reaches_the_disk(ev, cfg):
    put_log(ev, [("RPI/NUL.png", T1, "New"), ("RPI/a.png:s", T1, "New"), ("RPI/a.png.", T1, "New"), ("RPI/../x.png", T1, "New")])
    text = text_of(check(ev).get_data(as_text=True))
    assert "Unreadable Rows (4)" in text
    assert download(ev).status_code == 200 and not (cfg.data_dir / "RPI").exists() and history(cfg) == []


def test_downloading_only_reads_azure(ev, cfg):
    put(ev, "rpi/HOME_Look.png", PNG)
    put_log(ev, [("RPI/HOME_Look.png", T1, "New")])
    check(ev)
    download(ev)                                                        # the fake raises on any write operation
    assert set(azure_of(ev).calls) <= {"list_containers", "list", "download"}


def test_the_download_button_needs_login_csrf_and_a_known_event(ev, client, launched):
    assert ev.post("/events/1000/changes/rpi").status_code == 403
    assert ev.post("/events/9999/changes/rpi", data={"csrf_token": csrf_from(ev, "/events/1000/changes")}).status_code == 404
    assert client.post("/events/1000/changes/rpi").status_code == 403


def test_when_the_event_was_exported_again_nothing_is_downloaded(ev, cfg):
    put(ev, "rpi/HOME_Look.png", PNG)
    put_log(ev, [("RPI/HOME_Look.png", T1, "New")])
    check(ev)
    serve_event(azure_of(ev), dict(REAL_GUID_JSON, exportGuid="7c9e6679-7425-40de-944b-e07fc1f90ae7"))
    out = download(ev).get_data(as_text=True)
    assert "exported this event again" in out and not (cfg.data_dir / "RPI").exists() and history(cfg) == []
