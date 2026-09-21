"""Phase 7 - Azure listings and ranged reads, verified streaming writes, the asset folder setting, the Azure-list
comparison and the Table / LED download engine (Steps 7.1 - 7.3)."""

import hashlib
import os
import subprocess

import pytest
from azure.core.exceptions import ServiceRequestError
from conftest import db_rows
from fakes import ACCOUNT, FAKE_KEY, FakeBlobService
from phase6_helpers import T0, T1, T2, T3, parsed, real_structure

from ledsync.db import connect, init_db
from ledsync.services import assets, changes, exceptions, localfiles, rpi, transfer
from ledsync.services import settings as cs
from ledsync.services.progress import Progress
from ledsync.services.storage import AzureReadOnlyStorage, EventLocation, StorageError

FOLDER = "1000 - Star contender Doha"
LOC = EventLocation("2026", FOLDER)
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 20


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
    for event in ("1000", "2000"):
        c.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES (?, 'x', 'g')", (event,))
    c.commit()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch):
    monkeypatch.setattr(transfer, "RETRY_DELAY", 0)


# --- Azure listings and ranged reads ---------------------------------------------------------------------------------

def test_a_listing_carries_size_fingerprint_and_time(fake):
    blob(fake, "Table 1/inner/a.png", PNG)
    info = storage_for(fake).find_blob(LOC, "Table 1/Inner/A.PNG")
    assert info.path == "Table 1/inner/a.png" and info.size == len(PNG)
    assert info.md5 == hashlib.md5(PNG).digest() and info.modified == "2026-09-20T12:00:00Z"


def test_azure_may_have_no_fingerprint(fake):
    fake.no_md5 = True
    blob(fake, "rpi/a.png")
    assert storage_for(fake).find_blob(LOC, "RPI/a.png").md5 is None


def test_a_folder_is_listed_in_any_letter_case_and_only_its_files(fake):
    blob(fake, "Table 1/inner/a.png")
    blob(fake, "Table 1/inner/b.mp4")
    blob(fake, "Table 1/inner/sub/deep.png")
    blob(fake, "Table 1/outer/c.png")
    names = sorted(i.path for i in storage_for(fake).list_files(LOC, "Table 1/Inner"))
    assert names == ["Table 1/inner/a.png", "Table 1/inner/b.mp4"]


def test_a_missing_folder_has_no_files(fake):
    blob(fake, "Table 1/inner/a.png")
    assert storage_for(fake).list_files(LOC, "Table 9/Inner") == []


def test_two_folders_that_differ_only_in_case_are_refused(fake):
    blob(fake, "Table 1/inner/a.png")
    blob(fake, "Table 1/Inner/b.png")
    with pytest.raises(StorageError) as err:
        storage_for(fake).list_files(LOC, "Table 1/Inner")
    assert "More than one folder" in err.value.message


def test_unsafe_folder_paths_are_refused_before_any_call(fake):
    for bad in ("../x", "Table 1/../x", "/abs", "a\\b"):
        with pytest.raises(StorageError):
            storage_for(fake).list_files(LOC, bad)
    assert fake.calls == []


def test_many_lookups_share_listings(fake):
    for i in range(5):
        blob(fake, f"Table 1/inner/f{i}.png")
    storage, cache = storage_for(fake), {}
    storage.list_files(LOC, "Table 1/Inner", cache)
    for i in range(5):
        storage.find_blob(LOC, f"Table 1/Inner/F{i}.PNG", cache)
    assert fake.calls.count("list") == 3                    # event folder, Table 1, inner - each listed once


def test_a_file_is_read_in_ranges_never_whole(fake):
    data = os.urandom(10_000)
    blob(fake, "rpi/big.bin", data)
    got = b"".join(storage_for(fake).iter_blob(LOC, "rpi/big.bin", len(data), chunk_size=4096))
    assert got == data
    ranges = [(d[2], d[3]) for d in fake.downloads]
    assert ranges == [(0, 4096), (4096, 4096), (8192, 1808)]


def test_an_empty_file_needs_no_read(fake):
    blob(fake, "rpi/empty.png", b"")
    assert list(storage_for(fake).iter_blob(LOC, "rpi/empty.png", 0)) == [] and fake.downloads == []


def test_reading_a_missing_file_or_losing_the_connection_is_a_plain_error(fake):
    with pytest.raises(StorageError) as err:
        list(storage_for(fake).iter_blob(LOC, "rpi/nope.png", 10))
    assert err.value.category == exceptions.MISSING_FOLDER
    blob(fake, "rpi/a.png")
    fake.fail_with = ServiceRequestError("dns secret-host")
    with pytest.raises(StorageError) as err:
        list(storage_for(fake).iter_blob(LOC, "rpi/a.png", 10))
    assert err.value.category == exceptions.STORAGE_CONNECTIVITY and "secret-host" not in err.value.message


# --- verified streaming writes ----------------------------------------------------------------------------------------

def stream(data, size=4):
    return [data[i:i + size] for i in range(0, len(data), size)]


def test_a_streamed_file_is_written_verified_and_reports_progress(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    seen = []
    target = localfiles.write_stream(root, "a.png", stream(PNG), size=len(PNG), md5=hashlib.md5(PNG).digest(), on_bytes=seen.append)
    assert target.read_bytes() == PNG and sum(seen) == len(PNG) and sorted(p.name for p in root.iterdir()) == ["a.png"]


def test_a_wrong_size_is_discarded_and_the_old_copy_kept(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    (root / "a.png").write_bytes(b"old")
    with pytest.raises(localfiles.IntegrityError) as err:
        localfiles.write_stream(root, "a.png", stream(PNG[:-3]), size=len(PNG), md5=None)
    assert "size" in str(err.value) and (root / "a.png").read_bytes() == b"old" and sorted(p.name for p in root.iterdir()) == ["a.png"]


def test_a_wrong_checksum_is_discarded_and_the_old_copy_kept(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    (root / "a.png").write_bytes(b"old")
    with pytest.raises(localfiles.IntegrityError) as err:
        localfiles.write_stream(root, "a.png", stream(PNG), size=len(PNG), md5=hashlib.md5(b"other").digest())
    assert "checksum" in str(err.value) and (root / "a.png").read_bytes() == b"old" and not list(root.glob("*.tmp"))


def test_no_fingerprint_means_size_only(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    assert localfiles.write_stream(root, "a.png", stream(PNG), size=len(PNG), md5=None).read_bytes() == PNG


def test_an_empty_file_can_be_written(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    assert localfiles.write_stream(root, "a.png", [], size=0, md5=hashlib.md5(b"").digest()).read_bytes() == b""


def test_cancelling_between_chunks_leaves_nothing_behind(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    (root / "a.png").write_bytes(b"old")
    count = []
    with pytest.raises(localfiles.Cancelled):
        localfiles.write_stream(root, "a.png", stream(PNG), size=len(PNG), md5=None, on_bytes=count.append,
                                cancelled=lambda: len(count) >= 2)
    assert (root / "a.png").read_bytes() == b"old" and sorted(p.name for p in root.iterdir()) == ["a.png"]


def test_an_error_while_downloading_leaves_no_temporary_file(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")

    def broken():
        yield b"abc"
        raise StorageError(exceptions.STORAGE_CONNECTIVITY, "lost")
    with pytest.raises(StorageError):
        localfiles.write_stream(root, "a.png", broken(), size=10, md5=None)
    assert list(root.iterdir()) == []


def test_not_enough_disk_space_is_said_before_anything_is_written(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("w")
    monkeypatch.setattr(localfiles.shutil, "disk_usage", lambda p: type("U", (), {"free": 1000})())
    with pytest.raises(localfiles.LocalFileError) as err:
        localfiles.write_stream(root, "a.png", stream(PNG), size=10_000_000, md5=None)
    assert "free disk space" in str(err.value) and list(root.iterdir()) == []


def test_a_streamed_write_refuses_unsafe_names_and_folders_with_that_name(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    for bad in ("NUL", "a.png:s", "setup.exe", "LONGFI~1.PNG", "a/b.png"):
        with pytest.raises(localfiles.LocalFileError):
            localfiles.write_stream(root, bad, stream(PNG), size=len(PNG), md5=None)
    (root / "d.png").mkdir()
    with pytest.raises(localfiles.LocalFileError):
        localfiles.write_stream(root, "d.png", stream(PNG), size=len(PNG), md5=None)
    assert sorted(p.name for p in root.iterdir()) == ["d.png"]


# --- sub-folders ---------------------------------------------------------------------------------------------------------

def test_nested_folders_are_created_from_names_the_application_controls(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    made = localfiles.open_subfolder(root, "1000", "Table 1", "Main LED")
    assert made == root / "1000" / "Table 1" / "Main LED" and made.is_dir()
    assert localfiles.open_subfolder(root, "1000", "Table 1", "Main LED") == made


@pytest.mark.parametrize("bad", ["..", ".", "a/b", "a\\b", "NUL", "a:s", "a.", " a", "x" * 300, ""])
def test_unsafe_folder_names_are_refused_and_nothing_is_created(tmp_path_factory, bad):
    root = tmp_path_factory.mktemp("w")
    with pytest.raises(localfiles.LocalFileError):
        localfiles.open_subfolder(root, "1000", bad)
    assert list(root.iterdir()) == []


def test_a_file_or_junction_with_a_folder_name_is_refused(tmp_path_factory):
    root, other = tmp_path_factory.mktemp("w"), tmp_path_factory.mktemp("o")
    (root / "1000").write_text("a file")
    with pytest.raises(localfiles.LocalFileError):
        localfiles.open_subfolder(root, "1000", "Table 1")
    (root / "1000").unlink()
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "1000"), str(other)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("could not create a junction here")
    with pytest.raises(localfiles.LocalFileError):
        localfiles.open_subfolder(root, "1000", "Table 1")
    assert list(other.iterdir()) == []


def test_missing_files_are_found_in_one_step(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    (root / "here.png").write_bytes(b"x")
    assert localfiles.missing_files(root, ["here.png", "gone.png"]) == {"gone.png"}
    assert localfiles.missing_files(root / "nonexistent", ["a.png"]) == {"a.png"}


# --- the asset folder setting ---------------------------------------------------------------------------------------------

def test_the_default_asset_folder_is_events_inside_the_data_folder(conn, cfg):
    loaded = cs.load_asset_folder(conn, cfg.data_dir)
    assert loaded.is_default and loaded.effective == cfg.data_dir / "Events"


def test_both_folders_are_saved_together_and_reset_by_blanking(conn, cfg, tmp_path_factory):
    a, r = str(tmp_path_factory.mktemp("a") / "Events"), str(tmp_path_factory.mktemp("r") / "RPI")
    assert cs.save_folders(conn, r, a, cfg.data_dir) == ["RPI folder", "asset folder"]
    assert str(cs.load_asset_folder(conn, cfg.data_dir).effective) == a and str(cs.load_rpi_folder(conn, cfg.data_dir).effective) == r
    assert cs.save_folders(conn, r, a, cfg.data_dir) == []
    cs.save_folders(conn, "", "", cfg.data_dir)
    assert cs.load_asset_folder(conn, cfg.data_dir).is_default and cs.load_rpi_folder(conn, cfg.data_dir).is_default


def test_the_two_folders_must_be_different_and_not_nested(conn, cfg, tmp_path_factory):
    base = tmp_path_factory.mktemp("b")
    for rpi_text, asset_text in ((str(base / "x"), str(base / "x")), (str(base / "x"), str(base / "x" / "inner")),
                                 (str(base / "x" / "inner"), str(base / "x")), (str(base / "X"), str(base / "x"))):
        with pytest.raises(cs.SettingsError) as err:
            cs.save_folders(conn, rpi_text, asset_text, cfg.data_dir)
        assert "different folders" in str(err.value)
    assert cs.load_asset_folder(conn, cfg.data_dir).is_default


@pytest.mark.parametrize("bad", ["Events", "..\\x", "C:\\Windows\\Events", "C:\\", "C:\\a:b", "http://x/y"])
def test_unsafe_asset_folders_are_refused(conn, cfg, bad):
    with pytest.raises(cs.SettingsError) as err:
        cs.save_folders(conn, "", bad, cfg.data_dir)
    assert "default asset folder" in str(err.value)


def test_an_asset_folder_inside_the_data_folder_is_refused_and_the_change_is_audited(conn, cfg, tmp_path_factory):
    with pytest.raises(cs.SettingsError):
        cs.save_folders(conn, "", str(cfg.data_dir / "x"), cfg.data_dir)
    cs.save_folders(conn, "", str(tmp_path_factory.mktemp("m") / "E"), cfg.data_dir)
    assert db_rows(cfg, "SELECT message FROM operation_log WHERE operation = 'Settings Changed'")[-1]["message"] == \
        "Local folders saved (asset folder changed)."


# --- Azure's own file list, compared with the change log -----------------------------------------------------------------

def compare_with_azure(conn, fake, rows, local=None):
    local = changes.local_state(conn, "1000") if local is None else local
    base = changes.compare(parsed(rows), real_structure(), local)
    return changes.add_azure_files(base, storage_for(fake), LOC, real_structure(), local)


def test_files_in_azure_that_the_log_never_mentions_are_offered(conn, fake):
    blob(fake, "Table 2/inner/a.png")
    blob(fake, "Table 2/inner/b.mp4")
    result = compare_with_azure(conn, fake, [("Table 1/Inner/x.png", T0, "New")])
    extras = [a for a in result.assessments if not a.in_log]
    assert sorted(a.path for a in extras) == ["Table 2/inner/a.png", "Table 2/inner/b.mp4"]      # the REAL Azure paths
    assert all(a.action == changes.DOWNLOAD and a.label == changes.LABEL_NEW_UNLOGGED and a.table == 2 and a.led_type == "Inner"
               for a in extras)
    assert extras[0].cloud_time_text == "2026-09-20T12:00:00Z"


def test_the_change_log_always_wins_for_a_path_it_mentions(conn, fake):
    blob(fake, "Table 1/inner/a.png")
    blob(fake, "Table 1/inner/gone.png")
    result = compare_with_azure(conn, fake, [("Table 1/Inner/a.png", T1, "New"), ("Table 1/Inner/gone.png", T2, "Deleted")])
    assert not [a for a in result.assessments if not a.in_log]
    assert {a.file_name: a.action for a in result.assessments} == {"a.png": changes.DOWNLOAD, "gone.png": changes.DONE}


def test_placeholders_unsafe_names_and_non_assets_are_not_offered(conn, fake):
    for name in ("keepalive.txt", "README", "setup.exe", "desktop.ini", "NUL.png", "LONGFI~1.PNG", "a.png:s", ".hidden"):
        blob(fake, f"Table 2/inner/{name}")
    blob(fake, "Table 2/inner/ok.png")
    result = compare_with_azure(conn, fake, [])
    assert [a.file_name for a in result.assessments if not a.in_log] == ["ok.png"]


def test_only_enabled_tables_and_led_folders_are_listed(conn, fake):
    blob(fake, "Table 2/outer/x.png")            # Table 2 has no Outer in this event
    blob(fake, "Table 3/inner/y.png")
    blob(fake, "Table 1/mainled/z.png")
    result = compare_with_azure(conn, fake, [])
    assert [a.path for a in result.assessments] == ["Table 1/mainled/z.png"]
    assert not any(a.path.endswith(("x.png", "y.png")) for a in result.assessments)


def test_the_main_led_folder_is_found_under_either_spelling(conn, fake):
    blob(fake, "Table 1/Main LED/a.png")
    blob(fake, "Table 1/mainled/b.png")
    assert sorted(a.file_name for a in compare_with_azure(conn, fake, []).assessments) == ["a.png", "b.png"]


def test_a_file_already_downloaded_is_not_offered_again(conn, fake):
    blob(fake, "Table 2/inner/a.png")
    local = {changes.path_key(2, "Inner", "a.png"): changes.LocalRecord("Success", parsed([("Table 1/Inner/x.png", T0, "New")]).entries[0].timestamp)}
    a = compare_with_azure(conn, fake, [], local).assessments[0]
    assert a.action == changes.DONE and a.local_status == "Success" and not a.in_log


def test_names_that_would_be_one_file_on_disk_are_not_offered_twice(conn, fake):
    blob(fake, "Table 2/inner/Stra\u00dfe.png")
    result = compare_with_azure(conn, fake, [("Table 2/Inner/Strasse.png", T0, "New")])
    assert [a.path for a in result.assessments if not a.in_log] == []


def test_a_problem_listing_azure_is_reported_and_the_log_result_stands(conn, fake, monkeypatch):
    def broken(*a, **k):
        raise StorageError(exceptions.STORAGE_CONNECTIVITY, "Could not reach Azure Storage.")
    storage = storage_for(fake)
    monkeypatch.setattr(storage, "list_files", broken)
    base = changes.compare(parsed([("Table 1/Inner/x.png", T0, "New")]), real_structure(), {})
    result = changes.add_azure_files(base, storage, LOC, real_structure(), {})
    assert result.azure_note == "Could not reach Azure Storage." and [a.path for a in result.assessments] == ["Table 1/Inner/x.png"]


# --- the download engine ---------------------------------------------------------------------------------------------------

def comparison(conn, fake, rows, event="1000"):
    local = changes.local_state(conn, event)
    return changes.add_azure_files(changes.compare(parsed(rows), real_structure(), local), storage_for(fake), LOC, real_structure(), local)


def go(conn, fake, rows, root, data_dir, event="1000", progress=None):
    return assets.process(conn, storage_for(fake), ACCOUNT, LOC, event, comparison(conn, fake, rows, event), root, data_dir, progress)


def tree(root):
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())


def test_all_and_only_the_new_files_are_downloaded_into_the_event_table_led_structure(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    for path in ("Table 1/inner/a.png", "Table 1/outer/b.png", "Table 1/mainled/c.png", "Table 2/inner/d.png",
                 "Table 1/inner/not-in-log-and-not-wanted.exe", "Table 2/outer/e.png", "Table 3/inner/f.png"):
        blob(fake, path, path.encode())
    result = go(conn, fake, [("Table 1/Inner/a.png", T1, "New"), ("Table 1/Outer/b.png", T1, "New"),
                             ("Table 1/MainLED/c.png", T1, "New"), ("Table 2/Inner/d.png", T1, "New")], root, cfg.data_dir)
    assert (result.downloaded, result.failed, result.remaining) == (4, 0, 0)
    assert tree(root) == ["1000/Table 1/Inner/a.png", "1000/Table 1/Main LED/c.png", "1000/Table 1/Outer/b.png", "1000/Table 2/Inner/d.png"]
    assert sorted(p.name for p in (root / "1000").iterdir()) == ["Table 1", "Table 2"]                  # exactly the event's tables
    assert sorted(p.name for p in (root / "1000" / "Table 2").iterdir()) == ["Inner"]                    # and only its LED types
    assert (root / "1000" / "Table 1" / "Main LED" / "c.png").read_bytes() == b"Table 1/mainled/c.png"


def test_the_same_file_name_in_every_table_and_led_folder_stays_four_different_files(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    rows = []
    for table, led in ((1, "inner"), (1, "outer"), (2, "inner")):
        blob(fake, f"Table {table}/{led}/sponsorsequence.csv", f"{table}-{led}".encode())
        rows.append((f"Table {table}/{led.capitalize()}/sponsorsequence.csv", T1, "Updated"))
    assert go(conn, fake, rows, root, cfg.data_dir).downloaded == 3
    assert {(root / "1000" / f"Table {t}" / l / "sponsorsequence.csv").read_text() for t, l in ((1, "Inner"), (1, "Outer"), (2, "Inner"))} == \
        {"1-inner", "1-outer", "2-inner"}


def test_every_download_is_recorded_for_the_local_change_log(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/mainled/c.png")
    go(conn, fake, [("Table 1/MainLED/c.png", T1, "New")], root, cfg.data_dir)
    [row] = db_rows(cfg, "SELECT * FROM download_history")
    assert (row["event_id"], row["table_number"], row["led_type"], row["file_name"], row["status"], row["source_timestamp"]) == \
        ("1000", 1, "MainLED", "c.png", "Success", T1)
    assert row["local_path"] == str(root / "1000" / "Table 1" / "Main LED" / "c.png")
    assert row["source_path"] == f"https://{ACCOUNT}.blob.core.windows.net/2026/{FOLDER.replace(' ', '%20')}/Table%201/mainled/c.png"
    assert db_rows(cfg, "SELECT status FROM operation_log WHERE operation = 'Asset Download'") == [{"status": "Success"}]
    assert db_rows(cfg, "SELECT last_download FROM events WHERE event_id = '1000'")[0]["last_download"]


def test_a_second_run_downloads_nothing_and_an_update_replaces_only_that_file(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/a.png", b"one")
    blob(fake, "Table 1/inner/b.png", b"bee")
    rows = [("Table 1/Inner/a.png", T0, "New"), ("Table 1/Inner/b.png", T0, "New")]
    assert go(conn, fake, rows, root, cfg.data_dir).downloaded == 2
    assert go(conn, fake, rows, root, cfg.data_dir).total == 0
    blob(fake, "Table 1/inner/a.png", b"two")
    result = go(conn, fake, rows + [("Table 1/Inner/a.png", T1, "Updated")], root, cfg.data_dir)
    assert result.downloaded == 1 and (root / "1000" / "Table 1" / "Inner" / "a.png").read_bytes() == b"two"
    assert (root / "1000" / "Table 1" / "Inner" / "b.png").read_bytes() == b"bee"


def test_a_removal_in_the_cloud_deletes_the_local_copy_only(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/a.png")
    blob(fake, "Table 1/inner/keep.png")
    base = [("Table 1/Inner/a.png", T0, "New"), ("Table 1/Inner/keep.png", T0, "New")]
    go(conn, fake, base, root, cfg.data_dir)
    result = go(conn, fake, base + [("Table 1/Inner/a.png", T1, "Deleted")], root, cfg.data_dir)
    assert result.removed == 1 and tree(root) == ["1000/Table 1/Inner/keep.png"]
    assert (root / "1000" / "Table 1" / "Inner").is_dir()
    assert [r["status"] for r in db_rows(cfg, "SELECT status FROM download_history WHERE file_name = 'a.png'")] == ["Success", "Deleted"]
    blob(fake, "Table 1/inner/a.png", b"back")
    assert go(conn, fake, base + [("Table 1/Inner/a.png", T1, "Deleted"), ("Table 1/Inner/a.png", T2, "New")], root, cfg.data_dir).downloaded == 1


def test_a_removal_for_a_file_that_was_never_stored_is_recorded_without_error(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    local = {changes.path_key(1, "Inner", "a.png"): changes.LocalRecord("Success", parsed([("Table 1/Inner/x.png", T0, "New")]).entries[0].timestamp)}
    cmp = changes.compare(parsed([("Table 1/Inner/a.png", T1, "Deleted")]), real_structure(), local)
    result = assets.process(conn, storage_for(fake), ACCOUNT, LOC, "1000", cmp, root, cfg.data_dir)
    assert result.removed == 1 and result.failed == 0


def test_a_file_deleted_by_hand_is_downloaded_again(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/a.png")
    rows = [("Table 1/Inner/a.png", T0, "New")]
    go(conn, fake, rows, root, cfg.data_dir)
    (root / "1000" / "Table 1" / "Inner" / "a.png").unlink()
    cmp = comparison(conn, fake, rows)
    assert [i.file_name for i in assets.asset_items(cmp)] == []
    assert [i.file_name for i in assets.asset_items(cmp, root / "1000")] == ["a.png"]
    assert go(conn, fake, rows, root, cfg.data_dir).downloaded == 1


def test_a_bad_file_never_stops_the_others_and_is_retried_next_time(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/good.png")
    rows = [("Table 1/Inner/missing.png", T0, "New"), ("Table 1/Inner/good.png", T0, "New")]
    result = go(conn, fake, rows, root, cfg.data_dir)
    assert (result.downloaded, result.failed) == (1, 1) and "missing.png" in result.failures[0]
    assert sorted(h["status"] for h in db_rows(cfg, "SELECT status FROM download_history")) == ["Failure", "Success"]
    ex = db_rows(cfg, "SELECT category, table_number, led_type, file_name FROM exception_log")
    assert ex == [{"category": "Missing folder", "table_number": 1, "led_type": "Inner", "file_name": "missing.png"}]
    blob(fake, "Table 1/inner/missing.png")
    assert go(conn, fake, rows, root, cfg.data_dir).downloaded == 1


def test_a_corrupt_download_is_retried_once_then_reported_and_the_old_copy_stays(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/a.png", b"good v1")
    go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], root, cfg.data_dir)
    blob(fake, "Table 1/inner/a.png", b"good v2")
    fake.md5_overrides[f"{FOLDER}/Table 1/inner/a.png"] = hashlib.md5(b"something else").digest()
    result = go(conn, fake, [("Table 1/Inner/a.png", T0, "New"), ("Table 1/Inner/a.png", T1, "Updated")], root, cfg.data_dir)
    assert result.failed == 1 and "checksum" in result.failures[0]
    assert (root / "1000" / "Table 1" / "Inner" / "a.png").read_bytes() == b"good v1"
    assert len([d for d in fake.downloads if d[1].endswith("Table 1/inner/a.png")]) == 3                # 1 first run + 2 tries


def test_a_download_that_fails_once_is_retried_and_succeeds(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/a.png")
    original, state = fake._check, {"n": 0}

    def flaky(what):
        state["n"] += 1
        if what == "download" and not state.get("raised"):             # the first read of the file fails
            state["raised"] = True
            raise ServiceRequestError("blip")
        return original(what)
    fake._check = flaky
    assert go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], root, cfg.data_dir).downloaded == 1
    assert state["raised"]                                                # the failure really happened, and was survived


def test_a_file_larger_than_one_range_is_streamed_in_several_reads(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    data = os.urandom(9 * 1024 * 1024)
    blob(fake, "Table 1/inner/video.mp4", data)
    assert go(conn, fake, [("Table 1/Inner/video.mp4", T0, "New")], root, cfg.data_dir).downloaded == 1
    assert (root / "1000" / "Table 1" / "Inner" / "video.mp4").read_bytes() == data
    ranges = [d[3] for d in fake.downloads if d[1].endswith("video.mp4")]
    assert ranges == [4 * 1024 * 1024, 4 * 1024 * 1024, 1024 * 1024] and max(ranges) < len(data)


def test_azure_without_a_fingerprint_is_accepted_on_size_alone(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    fake.no_md5 = True
    blob(fake, "Table 1/inner/a.png")
    assert go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], root, cfg.data_dir).downloaded == 1


def test_files_found_only_in_azure_are_downloaded_too(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 2/inner/x.png", b"unlogged")
    blob(fake, "Table 2/inner/keepalive.txt")
    result = go(conn, fake, [("Table 1/Inner/other.png", T0, "Deleted")], root, cfg.data_dir)
    assert result.downloaded == 1 and tree(root) == ["1000/Table 2/Inner/x.png"]
    [row] = db_rows(cfg, "SELECT * FROM download_history")
    assert row["source_timestamp"] == "2026-09-20T12:00:00Z" and row["led_type"] == "Inner" and row["table_number"] == 2
    assert go(conn, fake, [("Table 1/Inner/other.png", T0, "Deleted")], root, cfg.data_dir).total == 0


def test_a_cancelled_run_stops_between_files_and_keeps_nothing_half_written(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    for n in "abc":
        blob(fake, f"Table 1/inner/{n}.png")
    progress = Progress()
    progress.start(3)
    original = progress.finish_file

    def cancel_after_first(ok):
        original(ok)
        progress.cancel()
    progress.finish_file = cancel_after_first
    result = go(conn, fake, [(f"Table 1/Inner/{n}.png", T0, "New") for n in "abc"], root, cfg.data_dir, progress=progress)
    assert result.cancelled and result.downloaded == 1 and result.remaining == 2
    assert len(tree(root)) == 1 and not [p for p in root.rglob("*.ledsync-tmp")]


def test_progress_reports_files_and_bytes(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/a.png", b"x" * 1000)
    blob(fake, "Table 1/inner/b.png", b"y" * 500)
    progress, seen = Progress(), []
    progress.start(2)
    original = progress.add_bytes
    progress.add_bytes = lambda n: (original(n), seen.append(progress.snapshot()["percent"]))
    go(conn, fake, [("Table 1/Inner/a.png", T0, "New"), ("Table 1/Inner/b.png", T0, "New")], root, cfg.data_dir, progress=progress)
    snap = progress.snapshot()
    assert snap["done"] == 2 and snap["failed"] == 0 and seen and max(seen) == 100


def test_a_connection_problem_stops_the_run_after_one_retry(conn, fake, cfg, tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("assets")
    for n in "abc":
        blob(fake, f"Table 1/inner/{n}.png")

    def offline(what):
        fake.calls.append(what)
        raise ServiceRequestError("no network")
    monkeypatch.setattr(fake, "_check", offline)
    result = go(conn, fake, [(f"Table 1/Inner/{n}.png", T0, "New") for n in "abc"], root, cfg.data_dir)
    assert result.stopped and result.failed == 1 and result.remaining == 2 and tree(root) == []


def test_an_unusable_or_dangerous_asset_folder_is_refused(conn, fake, cfg):
    blob(fake, "Table 1/inner/a.png")
    for bad in (cfg.data_dir, cfg.data_dir.parent, os.environ["SystemRoot"]):
        with pytest.raises(localfiles.LocalFileError):
            go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], bad, cfg.data_dir)
    assert db_rows(cfg, "SELECT COUNT(*) AS n FROM download_history")[0]["n"] == 0


def test_nothing_to_do_creates_no_folder(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets") / "Events"
    assert go(conn, fake, [("Table 1/Inner/x.png", T0, "Deleted")], root, cfg.data_dir).total == 0
    assert not root.exists()


def test_two_events_keep_separate_folders(conn, fake, cfg, tmp_path_factory):
    root = tmp_path_factory.mktemp("assets")
    blob(fake, "Table 1/inner/a.png", b"one")
    go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], root, cfg.data_dir, "1000")
    blob(fake, "Table 1/inner/a.png", b"two")
    go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], root, cfg.data_dir, "2000")
    assert (root / "1000" / "Table 1" / "Inner" / "a.png").read_bytes() == b"one"
    assert (root / "2000" / "Table 1" / "Inner" / "a.png").read_bytes() == b"two"


def test_downloading_only_reads_azure(conn, fake, cfg, tmp_path_factory):
    blob(fake, "Table 1/inner/a.png")
    go(conn, fake, [("Table 1/Inner/a.png", T0, "New")], tmp_path_factory.mktemp("assets"), cfg.data_dir)
    assert set(fake.calls) <= {"list_containers", "list", "download"}


def test_an_event_id_that_is_not_a_safe_folder_name_is_refused(conn, fake, cfg, tmp_path_factory):
    blob(fake, "Table 1/inner/a.png")
    cmp = comparison(conn, fake, [("Table 1/Inner/a.png", T0, "New")])
    with pytest.raises(localfiles.LocalFileError):
        assets.process(conn, storage_for(fake), ACCOUNT, LOC, "NUL", cmp, tmp_path_factory.mktemp("assets"), cfg.data_dir)
    with pytest.raises(localfiles.LocalFileError):
        assets.process(conn, storage_for(fake), ACCOUNT, LOC, "a..", cmp, tmp_path_factory.mktemp("assets"), cfg.data_dir)
