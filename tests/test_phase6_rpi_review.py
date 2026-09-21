"""Regression tests for the independent review of the RPI download addition (21/09/26)."""

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest
from azure.core.exceptions import ServiceRequestError
from conftest import csrf_from, db_rows, serve_event
from fakes import ACCOUNT, FAKE_KEY, FakeBlobService
from phase6_helpers import REAL_GUID_JSON, T0, T1, T2, csv_text, parsed, real_structure

from ledsync.db import connect, init_db
from ledsync.services import changelog, changes, exceptions, localfiles, mappings, rpi, transfer
from ledsync.services import settings as cs
from ledsync.services.storage import AzureReadOnlyStorage, EventLocation, StorageError

FOLDER = "1000 - Star contender Doha"
LOC = EventLocation("2026", FOLDER)
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 10


# --- finding 1: a folder is judged by WHAT it is, not how it is spelled ------------------------------------------------

@pytest.fixture
def data_dir_with_db(cfg):
    init_db(cfg.db_path)
    return cfg.data_dir


def alias(monkeypatch, other: Path):
    """Make `realpath` report a different-looking place, as it does for a mapped drive or `\\\\localhost\\C$` path."""
    monkeypatch.setattr(localfiles.os.path, "realpath", lambda p, **k: str(other))


def test_the_data_folder_is_refused_even_when_its_real_path_looks_like_somewhere_else(data_dir_with_db, tmp_path_factory, monkeypatch):
    alias(monkeypatch, tmp_path_factory.mktemp("elsewhere"))
    with pytest.raises(localfiles.LocalFileError) as err:
        localfiles.open_root(data_dir_with_db, data_dir_with_db)
    assert "reserved" in str(err.value)


def test_a_folder_that_contains_the_data_folder_is_refused(data_dir_with_db):
    for above in (data_dir_with_db.parent, data_dir_with_db.parent.parent):
        with pytest.raises(localfiles.LocalFileError):
            localfiles.open_root(above, data_dir_with_db)


def test_operating_system_folders_are_refused_by_identity_not_spelling(data_dir_with_db, tmp_path_factory, monkeypatch):
    alias(monkeypatch, tmp_path_factory.mktemp("elsewhere"))
    windows = Path(os.environ["SystemRoot"])
    for folder in (windows, windows / "System32"):
        with pytest.raises(localfiles.LocalFileError):
            localfiles.open_root(folder, data_dir_with_db)


def test_a_drive_root_is_refused(data_dir_with_db):
    with pytest.raises(localfiles.LocalFileError):
        localfiles.open_root(Path(os.environ["SystemDrive"] + "\\"), data_dir_with_db)


def test_a_folder_where_the_database_shows_up_as_a_plain_file_is_refused(data_dir_with_db, tmp_path_factory):
    other = tmp_path_factory.mktemp("linked")
    try:
        os.link(data_dir_with_db / "ledsync.db", other / "ledsync.db")            # a second name for the same file
    except OSError:
        pytest.skip("hard links are not available here")
    with pytest.raises(localfiles.LocalFileError):
        localfiles.open_root(other, data_dir_with_db)


def test_a_real_mapped_drive_to_the_data_folder_is_refused(data_dir_with_db):
    made = subprocess.run(["subst", "R:", str(data_dir_with_db)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("could not create a drive alias here")
    try:
        with pytest.raises(localfiles.LocalFileError):
            localfiles.open_root(Path("R:\\"), data_dir_with_db)
    finally:
        subprocess.run(["subst", "R:", "/D"], capture_output=True)


@pytest.mark.parametrize("host", ["0", "127.1", "2130706433", "0x7f000001", "0177.0.0.1", "127.255.255.254", "localhost", "LOCALHOST.local"])
def test_every_way_of_writing_this_computer_is_recognised(host):
    assert mappings._is_this_computer(host)


@pytest.mark.parametrize("host", ["device01", "10.0.0.5", "192.168.1.20", "1.2.3.4", "256.1.1.1", "a.b"])
def test_other_hosts_are_not_mistaken_for_this_computer(host):
    assert not mappings._is_this_computer(host)


def test_conin_and_conout_are_reserved_names_in_folders_too():
    for name in ("CONIN$", "CONOUT$"):
        with pytest.raises(mappings.MappingError):
            mappings.validate_shared_folder("C:\\" + name, "T")


# --- finding 2: another event's file in the shared RPI folder is never clobbered or removed ----------------------------

@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    for event in ("1000", "2000"):
        c.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES (?, 'x', 'g')", (event,))
    c.commit()
    yield c
    c.close()


@pytest.fixture
def fake():
    f = FakeBlobService()
    f.containers.add("2026")
    return f


def storage_for(fake):
    return AzureReadOnlyStorage(ACCOUNT, FAKE_KEY, service_factory=fake.factory)


def blob(fake, path, data=PNG):
    fake.put("2026", f"{FOLDER}/{path}", data)


def run(conn, fake, rows, root, data_dir, event="1000"):
    comparison = changes.compare(parsed(rows), real_structure(), changes.local_state(conn, event))
    return rpi.process(conn, storage_for(fake), ACCOUNT, LOC, event, comparison, root, data_dir)


def test_each_event_has_its_own_rpi_sub_folder_so_events_never_touch_each_others_files(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("rpi")
    blob(fake, "rpi/HOME_Look.png", b"event one")
    assert run(conn, fake, [("RPI/HOME_Look.png", T1, "New")], root, cfg.data_dir, "1000").downloaded == 1
    blob(fake, "rpi/HOME_Look.png", b"event two")
    assert run(conn, fake, [("RPI/HOME_Look.png", T1, "New")], root, cfg.data_dir, "2000").downloaded == 1
    assert (root / "1000" / "HOME_Look.png").read_bytes() == b"event one"
    assert (root / "2000" / "HOME_Look.png").read_bytes() == b"event two"


def test_one_events_removal_never_touches_another_events_file(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("rpi")
    blob(fake, "rpi/HOME_Look.png", b"event one")
    run(conn, fake, [("RPI/HOME_Look.png", T1, "New")], root, cfg.data_dir, "1000")
    blob(fake, "rpi/HOME_Look.png", b"event two")
    run(conn, fake, [("RPI/HOME_Look.png", T1, "New")], root, cfg.data_dir, "2000")
    result = run(conn, fake, [("RPI/HOME_Look.png", T1, "New"), ("RPI/HOME_Look.png", T2, "Deleted")], root, cfg.data_dir, "2000")
    assert result.removed == 1
    assert not (root / "2000" / "HOME_Look.png").exists() and (root / "1000" / "HOME_Look.png").read_bytes() == b"event one"


def test_the_same_event_can_still_replace_its_own_file(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("rpi")
    blob(fake, "rpi/a.png", b"one")
    run(conn, fake, [("RPI/a.png", T0, "New")], root, cfg.data_dir)
    blob(fake, "rpi/a.png", b"two")
    assert run(conn, fake, [("RPI/a.png", T0, "New"), ("RPI/a.png", T1, "Updated")], root, cfg.data_dir).downloaded == 1
    assert (root / "1000" / "a.png").read_bytes() == b"two"


# --- finding 3: a file that is no longer on disk (or a changed folder) is downloaded again -----------------------------

def test_a_file_deleted_by_hand_is_downloaded_again(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("rpi")
    blob(fake, "rpi/a.png")
    run(conn, fake, [("RPI/a.png", T1, "New")], root, cfg.data_dir)
    (root / "1000" / "a.png").unlink()
    comparison = changes.compare(parsed([("RPI/a.png", T1, "New")]), real_structure(), changes.local_state(conn, "1000"))
    assert [i.file_name for i in rpi.rpi_items(comparison)] == []                          # the history alone says "done"
    assert [i.file_name for i in rpi.rpi_items(comparison, root / "1000")] == ["a.png"]    # the folder says otherwise
    assert run(conn, fake, [("RPI/a.png", T1, "New")], root, cfg.data_dir).downloaded == 1
    assert (root / "1000" / "a.png").read_bytes() == PNG


def test_a_changed_rpi_folder_receives_the_files_again(conn, fake, cfg, tmp_path_factory):
    old, new = tmp_path_factory.mktemp("old"), tmp_path_factory.mktemp("new")
    blob(fake, "rpi/a.png")
    run(conn, fake, [("RPI/a.png", T1, "New")], old, cfg.data_dir)
    assert run(conn, fake, [("RPI/a.png", T1, "New")], new, cfg.data_dir).downloaded == 1
    assert (new / "1000" / "a.png").exists() and (old / "1000" / "a.png").exists()


def test_a_file_that_is_present_or_was_removed_on_purpose_is_left_alone(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("rpi")
    blob(fake, "rpi/a.png")
    run(conn, fake, [("RPI/a.png", T1, "New")], root, cfg.data_dir)
    assert run(conn, fake, [("RPI/a.png", T1, "New")], root, cfg.data_dir).total == 0                    # present: nothing to do
    run(conn, fake, [("RPI/a.png", T0, "New"), ("RPI/a.png", T2, "Deleted")], root, cfg.data_dir)      # removed in the cloud
    assert not (root / "1000" / "a.png").exists()
    assert run(conn, fake, [("RPI/a.png", T0, "New"), ("RPI/a.png", T2, "Deleted")], root, cfg.data_dir).total == 0   # not resurrected


# --- finding 4: an over-long folder is refused, never silently cut short -------------------------------------------------

def test_an_over_long_folder_is_refused_not_truncated(logged_in, cfg):
    long_path = "C:\\" + "\\".join(["abcdefghij"] * 25)                       # 250+ characters
    resp = logged_in.post("/settings/folders", data={"rpi_folder": long_path, "csrf_token": csrf_from(logged_in, "/settings/folders")})
    assert resp.status_code == 400 and "too long" in resp.get_data(as_text=True)
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    assert cs.load_rpi_folder(conn, cfg.data_dir).is_default
    conn.close()


def test_the_folder_box_allows_typing_more_than_the_limit_so_it_can_be_refused_clearly(logged_in):
    assert 'maxlength="300"' in logged_in.get("/settings/folders").get_data(as_text=True)


# --- finding 5: names that can run code or make Windows contact another computer are not accepted -------------------

@pytest.mark.parametrize("name", ["setup.exe", "A.EXE", "x.dll", "run.bat", "go.cmd", "s.ps1", "m.vbs", "j.js", "p.hta", "i.msi",
                                  "k.scr", "shortcut.lnk", "web.url", "steal.scf", "x.library-ms", "x.searchConnector-ms",
                                  "desktop.ini", "DESKTOP.INI", "autorun.inf", "Thumbs.db", "double.png.exe"])
def test_dangerous_file_types_are_skipped_by_the_parser_and_refused_by_the_writer(tmp_path, name):
    result = parsed([("RPI/" + name, T0, "New"), ("RPI/ok.png", T0, "New")])
    assert [e.path for e in result.entries] == ["RPI/ok.png"] and "not accepted" in result.skipped[0].reason
    with pytest.raises(localfiles.LocalFileError):
        localfiles.write_atomic(tmp_path, name, b"x")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["a.png", "a.jpg", "a.mp4", "a.csv", "a.txt", "a.gif", "a.webp", "a.mov", "a.json", "exe.png", "js.txt"])
def test_ordinary_media_and_data_files_are_still_accepted(name):
    assert len(parsed([("RPI/" + name, T0, "New")]).entries) == 1


# --- finding 6: an 8.3 short name is not accepted as a stand-in for another file --------------------------------------

@pytest.mark.parametrize("name", ["LONGFI~1.PNG", "longfi~1.png", "PROGRA~1", "A~1.TXT", "abcdef~12.jpg"])
def test_short_name_aliases_are_skipped_and_never_touch_another_file(tmp_path, name):
    assert parsed([("RPI/" + name, T0, "New")]).skipped
    (tmp_path / "Longfilename.png").write_bytes(b"precious")
    for action in (lambda: localfiles.write_atomic(tmp_path, name, b"x"), lambda: localfiles.delete_file(tmp_path, name)):
        with pytest.raises(localfiles.LocalFileError):
            action()
    assert (tmp_path / "Longfilename.png").read_bytes() == b"precious"


@pytest.mark.parametrize("name", ["my~photo.png", "holiday~1.png", "a~b.png", "x~y", "tilde~.png"])
def test_ordinary_names_with_a_tilde_are_still_fine(name):
    assert len(parsed([("RPI/" + name, T0, "New")]).entries) == 1


# --- finding 7: a connection problem ends the run at once, and a run has a size limit ------------------------------------

def test_a_connection_problem_stops_the_run_instead_of_trying_every_file(conn, fake, cfg, tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("rpi")
    for n in "abc":
        blob(fake, f"rpi/{n}.png")
    monkeypatch.setattr(transfer, "RETRY_DELAY", 0)

    def offline(what):
        fake.calls.append(what)
        raise ServiceRequestError("no network")
    monkeypatch.setattr(fake, "_check", offline)
    result = run(conn, fake, [(f"RPI/{n}.png", T1, "New") for n in "abc"], root, cfg.data_dir)
    assert result.stopped and result.failed == 1 and result.remaining == 2 and result.downloaded == 0
    assert exceptions.STORAGE_CONNECTIVITY in [r["category"] for r in db_rows(cfg, "SELECT category FROM exception_log")]
    assert len(fake.calls) == 2                                        # the first file, tried twice - then the run stopped


def test_a_missing_blob_does_not_stop_the_run(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("rpi")
    blob(fake, "rpi/good.png")
    result = run(conn, fake, [("RPI/missing.png", T1, "New"), ("RPI/good.png", T1, "New")], root, cfg.data_dir)
    assert result.failed == 1 and result.downloaded == 1 and not result.stopped


def test_one_run_handles_a_limited_number_of_files_and_the_next_run_continues(conn, fake, cfg, tmp_path_factory, monkeypatch):
    monkeypatch.setattr(transfer, "MAX_FILES_PER_RUN", 2)
    root = tmp_path_factory.mktemp("rpi")
    names = [f"f{i}.png" for i in range(5)]
    for n in names:
        blob(fake, f"rpi/{n}")
    rows = [(f"RPI/{n}", T1, "New") for n in names]
    first = run(conn, fake, rows, root, cfg.data_dir)
    assert (first.downloaded, first.remaining) == (2, 3)
    second = run(conn, fake, rows, root, cfg.data_dir)
    third = run(conn, fake, rows, root, cfg.data_dir)
    assert (second.downloaded, second.remaining, third.downloaded, third.remaining) == (2, 1, 1, 0)
    assert sorted(p.name for p in (root / "1000").iterdir()) == sorted(names)


def test_finding_blob_names_shares_directory_listings_across_files(fake):
    for i in range(6):
        blob(fake, f"rpi/f{i}.png")
    cache = {}
    for i in range(6):
        assert storage_for(fake).resolve_relative_path(LOC, f"RPI/F{i}.PNG", cache) == f"rpi/f{i}.png"
    assert fake.calls.count("list") == 2                               # the event folder once, the rpi folder once


# --- notes ------------------------------------------------------------------------------------------------------------------

def test_a_folder_named_rpi_and_another_named_RPI_only_matter_if_the_file_is_in_both(fake):
    blob(fake, "rpi/x.png")
    blob(fake, "RPI/other.png")
    assert storage_for(fake).resolve_relative_path(LOC, "RPI/x.png") == "rpi/x.png"
    blob(fake, "RPI/x.png")
    with pytest.raises(StorageError) as err:
        storage_for(fake).resolve_relative_path(LOC, "RPI/x.png")
    assert "More than one file" in err.value.message


def test_data_is_flushed_to_disk_before_it_takes_the_real_name(tmp_path, monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(localfiles.os, "fsync", lambda fd: calls.append(fd) or real(fd))
    localfiles.write_atomic(tmp_path, "a.png", b"x")
    assert len(calls) == 1


def test_old_temporary_files_from_a_crash_are_swept_but_a_fresh_one_is_kept(tmp_path_factory, cfg):
    tmp_path = tmp_path_factory.mktemp("sweep")
    old, fresh = tmp_path / ".aaa.ledsync-tmp", tmp_path / ".bbb.ledsync-tmp"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    (tmp_path / "keep.png").write_bytes(b"x")
    two_hours_ago = time.time() - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))
    localfiles.open_root(tmp_path, cfg.data_dir)
    assert sorted(p.name for p in tmp_path.iterdir()) == [".bbb.ledsync-tmp", "keep.png"]


def test_a_read_only_file_gives_a_specific_message_for_write_and_delete(tmp_path):
    target = tmp_path / "a.png"
    target.write_bytes(b"old")
    os.chmod(target, stat.S_IREAD)
    try:
        with pytest.raises(localfiles.LocalFileError) as write_err:
            localfiles.write_atomic(tmp_path, "a.png", b"new")
        with pytest.raises(localfiles.LocalFileError) as delete_err:
            localfiles.delete_file(tmp_path, "a.png")
    finally:
        os.chmod(target, stat.S_IWRITE)
    assert "read-only" in str(write_err.value) and "read-only" in str(delete_err.value)
    assert target.read_bytes() == b"old" and sorted(p.name for p in tmp_path.iterdir()) == ["a.png"]


def test_a_disk_error_still_says_to_free_space(tmp_path, monkeypatch):
    def full(fd):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(localfiles.os, "fsync", full)
    with pytest.raises(localfiles.LocalFileError) as err:
        localfiles.write_atomic(tmp_path, "a.png", b"x")
    assert "free disk space" in str(err.value) and list(tmp_path.iterdir()) == []


def test_work_on_a_folder_that_does_not_answer_gives_up_instead_of_freezing():
    started = time.monotonic()
    with pytest.raises(localfiles.LocalFileError) as err:
        localfiles.run_limited(lambda: time.sleep(3), 0.2)
    assert time.monotonic() - started < 1.5 and "did not respond" in str(err.value)


# --- the page: wording and counts -------------------------------------------------------------------------------------------

@pytest.fixture
def ev(logged_in):
    serve_event(logged_in.application.extensions["test.azure"], dict(REAL_GUID_JSON))
    logged_in.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(logged_in, "/events/new")})
    return logged_in


def test_the_button_counts_files_missing_from_the_folder(ev, cfg):
    az = ev.application.extensions["test.azure"]
    az.put("2026", f"{FOLDER}/rpi/HOME_Look.png", PNG)
    az.put("2026", f"{FOLDER}/_ledassetschangelog.csv", csv_text([("RPI/HOME_Look.png", T1, "New")]))
    token = lambda: csrf_from(ev, "/events/1000/changes")           # noqa: E731
    ev.post("/events/1000/changes/check", data={"csrf_token": token()})
    ev.post("/events/1000/changes/download", data={"csrf_token": token()})
    assert "Nothing is waiting to be downloaded." in ev.get("/events/1000/changes").get_data(as_text=True)
    (cfg.data_dir / "RPI" / "1000" / "HOME_Look.png").unlink()
    assert "DOWNLOAD FILES (1)" in ev.get("/events/1000/changes").get_data(as_text=True)
    out = ev.post("/events/1000/changes/download", data={"csrf_token": token()}, follow_redirects=True).get_data(as_text=True)
    assert "Downloaded 1 file(s)" in out and (cfg.data_dir / "RPI" / "1000" / "HOME_Look.png").read_bytes() == PNG
