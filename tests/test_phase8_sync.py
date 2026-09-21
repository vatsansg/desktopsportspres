"""Phase 8 - pushing downloaded files to the mapped LED folders, the event status, and Download & Sync (Steps 8.1, 8.2)."""

import csv
import hashlib
import os
import stat
import subprocess
import threading

import pytest
from conftest import csrf_from, db_rows, serve_event
from phase6_helpers import REAL_GUID_JSON, T0, T1, csv_text, real_structure

from ledsync.db import connect, init_db
from ledsync.services import connectivity, eventstatus, exceptions, localchangelog, localfiles, mappings, sync, transfer
from ledsync.services.progress import Progress

FOLDER = "1000 - Star contender Doha"
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 20


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch):
    monkeypatch.setattr(transfer, "RETRY_DELAY", 0)


# --- a small world: event 1000, mapped LEDs, downloaded local files ------------------------------------------------------------

@pytest.fixture
def world(cfg, tmp_path_factory):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    conn.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('1000', 'x', 'g')")
    mappings.reconcile(conn, "1000", real_structure().enabled_pairs)
    conn.commit()
    devices = {key: tmp_path_factory.mktemp("dev_" + key) for key in ("t1i", "t1o", "t1m", "t2i")}
    mappings.save_mappings(conn, "1000", real_structure(), {
        (1, "Inner"): ("", str(devices["t1i"])), (1, "Outer"): ("", str(devices["t1o"])),
        (1, "MainLED"): ("", str(devices["t1m"])), (2, "Inner"): ("", str(devices["t2i"]))}, forbidden_roots=(cfg.data_dir,))
    assets = tmp_path_factory.mktemp("assets") / "1000"
    yield conn, devices, assets
    conn.close()


def local_file(conn, assets, table, led, name, data=PNG, *, downloaded_at="2026-09-20T10:00:00Z", status="Success"):
    """A file the download phase would have stored, with its history row."""
    from ledsync.services import structure
    folder = assets / f"Table {table}" / structure.LED_LABELS[led]
    folder.mkdir(parents=True, exist_ok=True)
    if status == "Success":
        (folder / name).write_bytes(data)
    conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, source_path, local_path, "
                 "source_timestamp, download_timestamp, status) VALUES ('1000', ?, ?, ?, 'src', ?, '2026-09-17T06:00:00Z', ?, ?)",
                 (name, table, led, str(folder / name), downloaded_at, status))
    conn.commit()


def run(conn, assets, cfg, **kw):
    items, unmapped = sync.plan(conn, "1000", assets)
    result = sync.process(conn, "1000", items, cfg.data_dir, kw.pop("progress", None))
    result.unmapped = unmapped
    return result


def sync_rows(cfg):
    return db_rows(cfg, "SELECT file_name, destination, status, error_message FROM sync_history ORDER BY sync_id")


# --- planning: what needs sending ---------------------------------------------------------------------------------------------

def test_new_files_are_planned_in_table_led_name_order(world):
    conn, devices, assets = world
    local_file(conn, assets, 2, "Inner", "z.png")
    local_file(conn, assets, 1, "Outer", "b.png")
    local_file(conn, assets, 1, "Inner", "B.png")
    local_file(conn, assets, 1, "Inner", "a.png")
    items, unmapped = sync.plan(conn, "1000", assets)
    assert [(i.table, i.led_type, i.file_name, i.action) for i in items] == [
        (1, "Inner", "a.png", "Push"), (1, "Inner", "B.png", "Push"), (1, "Outer", "b.png", "Push"), (2, "Inner", "z.png", "Push")]
    assert unmapped == [] and items[0].source == assets / "Table 1" / "Inner" / "a.png"
    assert items[0].destination == os.path.join(str(devices["t1i"]), "a.png")


def test_rpi_files_and_unenabled_leds_are_never_planned(world):
    conn, devices, assets = world
    conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, status, download_timestamp, source_timestamp) "
                 "VALUES ('1000', 'rpi.png', NULL, 'RPI', 'Success', '2026-09-20T10:00:00Z', '2026-09-17T06:00:00Z')")
    conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, status, download_timestamp, source_timestamp) "
                 "VALUES ('1000', 'x.png', 2, 'Outer', 'Success', '2026-09-20T10:00:00Z', '2026-09-17T06:00:00Z')")
    conn.commit()
    assert sync.plan(conn, "1000", assets) == ([], [])


def test_an_led_with_files_but_no_device_folder_is_reported_not_planned(world):
    conn, devices, assets = world
    mappings.save_mappings(conn, "1000", real_structure(), {(1, "Outer"): ("", "")}, forbidden_roots=())
    local_file(conn, assets, 1, "Outer", "a.png")
    items, unmapped = sync.plan(conn, "1000", assets)
    assert items == [] and unmapped == ["Table 1 Outer"]


def test_a_file_already_sent_is_not_planned_again_until_it_is_downloaded_again(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png", downloaded_at="2026-09-20T10:00:00Z")
    assert run(conn, assets, cfg).pushed == 1
    assert sync.plan(conn, "1000", assets)[0] == []
    conn.execute("UPDATE sync_history SET sync_timestamp = '2026-09-20T09:00:00Z'")           # the push predates the download
    conn.commit()
    assert [i.file_name for i in sync.plan(conn, "1000", assets)[0]] == ["a.png"]


def test_a_failed_push_is_planned_again(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    (assets / "Table 1" / "Inner" / "a.png").unlink()                       # the local copy is missing: the push fails
    assert run(conn, assets, cfg).failed == 1
    assert [i.file_name for i in sync.plan(conn, "1000", assets)[0]] == ["a.png"]


def test_removals_are_planned_only_for_files_this_application_sent(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "ours.png")
    run(conn, assets, cfg)
    local_file(conn, assets, 1, "Inner", "ours.png", status="Deleted", downloaded_at="2026-09-21T10:00:00Z")
    local_file(conn, assets, 1, "Inner", "never-sent.png", status="Deleted")
    items = sync.plan(conn, "1000", assets)[0]
    assert [(i.file_name, i.action) for i in items] == [("ours.png", "Remove")]


def test_a_changed_device_folder_means_everything_is_planned_for_the_new_folder(world, cfg, tmp_path_factory):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    run(conn, assets, cfg)
    new = tmp_path_factory.mktemp("newdev")
    mappings.save_mappings(conn, "1000", real_structure(), {(1, "Inner"): ("", str(new))}, forbidden_roots=(cfg.data_dir,))
    items = sync.plan(conn, "1000", assets)[0]
    assert [i.destination for i in items] == [os.path.join(str(new), "a.png")]


def test_destination_matching_ignores_case(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    run(conn, assets, cfg)
    conn.execute("UPDATE sync_history SET destination = upper(destination)")
    conn.commit()
    assert sync.plan(conn, "1000", assets)[0] == []


# --- copying one file safely ---------------------------------------------------------------------------------------------------

def test_a_file_is_copied_verified_and_leaves_no_temporary_file(tmp_path_factory):
    src_dir, dev = tmp_path_factory.mktemp("s"), tmp_path_factory.mktemp("d")
    (src_dir / "a.png").write_bytes(PNG)
    seen = []
    target = localfiles.push_file(src_dir / "a.png", dev, "a.png", on_bytes=seen.append)
    assert target.read_bytes() == PNG and sum(seen) == len(PNG) and sorted(p.name for p in dev.iterdir()) == ["a.png"]


def test_a_large_file_is_copied_in_pieces(tmp_path_factory):
    src_dir, dev = tmp_path_factory.mktemp("s"), tmp_path_factory.mktemp("d")
    data = os.urandom(9 * 1024 * 1024)
    (src_dir / "v.mp4").write_bytes(data)
    calls = []
    localfiles.push_file(src_dir / "v.mp4", dev, "v.mp4", on_bytes=calls.append)
    assert (dev / "v.mp4").read_bytes() == data and len(calls) == 3


def test_a_file_with_the_same_name_that_we_did_not_put_there_is_replaced(tmp_path_factory):
    src_dir, dev = tmp_path_factory.mktemp("s"), tmp_path_factory.mktemp("d")
    (src_dir / "a.png").write_bytes(b"new content")
    (dev / "a.png").write_bytes(b"somebody else's file")
    (dev / "other.txt").write_bytes(b"untouched")
    localfiles.push_file(src_dir / "a.png", dev, "a.png")
    assert (dev / "a.png").read_bytes() == b"new content" and (dev / "other.txt").read_bytes() == b"untouched"


def test_the_copy_is_checked_before_it_takes_its_name_and_a_bad_copy_is_discarded(tmp_path_factory, monkeypatch):
    src_dir, dev = tmp_path_factory.mktemp("s"), tmp_path_factory.mktemp("d")
    (src_dir / "a.png").write_bytes(PNG)
    (dev / "a.png").write_bytes(b"old good")

    class HalfWriter:
        def __init__(self, handle):
            self.h = handle

        def __enter__(self):
            self.h.__enter__()
            return self

        def __exit__(self, *a):
            return self.h.__exit__(*a)

        def write(self, data):
            return self.h.write(data[: len(data) // 2])                          # the device silently loses half the data

        def __getattr__(self, name):
            return getattr(self.h, name)
    real_open = open

    def flaky_open(path, mode="r", *a, **k):
        handle = real_open(path, mode, *a, **k)
        return HalfWriter(handle) if "x" in mode and str(path).endswith(".ledsync-tmp") else handle
    monkeypatch.setattr(localfiles, "open", flaky_open, raising=False)
    with pytest.raises(localfiles.IntegrityError):
        localfiles.push_file(src_dir / "a.png", dev, "a.png")
    assert (dev / "a.png").read_bytes() == b"old good" and sorted(p.name for p in dev.iterdir()) == ["a.png"]


def test_a_missing_source_file_is_a_plain_error(tmp_path_factory):
    dev = tmp_path_factory.mktemp("d")
    with pytest.raises(localfiles.IntegrityError) as err:
        localfiles.push_file(dev / "nope.png", dev, "a.png")
    assert "missing from this computer" in str(err.value) and list(dev.iterdir()) == []


def test_a_device_folder_that_does_not_exist_is_reported_and_never_created(tmp_path_factory):
    src_dir = tmp_path_factory.mktemp("s")
    (src_dir / "a.png").write_bytes(PNG)
    gone = src_dir / "no-such-device"
    with pytest.raises(localfiles.DeviceError) as err:
        localfiles.push_file(src_dir / "a.png", gone, "a.png")
    assert err.value.category == exceptions.MISSING_FOLDER and not gone.exists()


def test_a_read_only_file_on_the_device_gives_a_specific_message(tmp_path_factory):
    src_dir, dev = tmp_path_factory.mktemp("s"), tmp_path_factory.mktemp("d")
    (src_dir / "a.png").write_bytes(b"new")
    (dev / "a.png").write_bytes(b"old")
    os.chmod(dev / "a.png", stat.S_IREAD)
    try:
        with pytest.raises(localfiles.DeviceError) as err:
            localfiles.push_file(src_dir / "a.png", dev, "a.png")
    finally:
        os.chmod(dev / "a.png", stat.S_IWRITE)
    assert err.value.category == exceptions.PERMISSION and "read-only" in str(err.value)
    assert (dev / "a.png").read_bytes() == b"old" and sorted(p.name for p in dev.iterdir()) == ["a.png"]


def test_unsafe_names_and_folders_with_that_name_are_refused(tmp_path_factory):
    src_dir, dev = tmp_path_factory.mktemp("s"), tmp_path_factory.mktemp("d")
    (src_dir / "a.png").write_bytes(PNG)
    for bad in ("NUL", "a.png:s", "setup.exe", "LONGFI~1.PNG", "a/b.png"):
        with pytest.raises(localfiles.LocalFileError):
            localfiles.push_file(src_dir / "a.png", dev, bad)
    (dev / "d.png").mkdir()
    with pytest.raises(localfiles.LocalFileError):
        localfiles.push_file(src_dir / "a.png", dev, "d.png")
    assert sorted(p.name for p in dev.iterdir()) == ["d.png"]


def test_cancelling_leaves_no_temporary_file_and_the_old_file_on_the_device(tmp_path_factory):
    src_dir, dev = tmp_path_factory.mktemp("s"), tmp_path_factory.mktemp("d")
    (src_dir / "v.mp4").write_bytes(os.urandom(9 * 1024 * 1024))
    (dev / "v.mp4").write_bytes(b"old")
    n = []
    with pytest.raises(localfiles.Cancelled):
        localfiles.push_file(src_dir / "v.mp4", dev, "v.mp4", on_bytes=n.append, cancelled=lambda: len(n) >= 1)
    assert (dev / "v.mp4").read_bytes() == b"old" and sorted(p.name for p in dev.iterdir()) == ["v.mp4"]


def test_not_enough_space_on_the_device_is_said_before_copying(tmp_path_factory, monkeypatch):
    src_dir, dev = tmp_path_factory.mktemp("s"), tmp_path_factory.mktemp("d")
    (src_dir / "a.png").write_bytes(PNG)
    monkeypatch.setattr(localfiles.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    with pytest.raises(localfiles.DeviceError) as err:
        localfiles.push_file(src_dir / "a.png", dev, "a.png")
    assert "free space" in str(err.value) and list(dev.iterdir()) == []


def test_a_device_folder_that_is_or_aliases_the_data_folder_is_refused(cfg, tmp_path_factory):
    init_db(cfg.db_path)
    for bad in (cfg.data_dir, cfg.data_dir.parent, os.environ["SystemRoot"]):
        with pytest.raises(localfiles.DeviceError):
            localfiles.check_destination(bad, cfg.data_dir)
    good = tmp_path_factory.mktemp("dev")
    assert localfiles.check_destination(good, cfg.data_dir) == good
    with pytest.raises(localfiles.DeviceError) as err:
        localfiles.check_destination(good / "nope", cfg.data_dir)
    assert err.value.category == exceptions.MISSING_FOLDER


def test_a_junction_alias_of_the_data_folder_is_refused_as_a_device_folder(cfg, tmp_path_factory):
    init_db(cfg.db_path)
    link = tmp_path_factory.mktemp("j") / "link"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(cfg.data_dir)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("could not create a junction here")
    with pytest.raises(localfiles.DeviceError):
        localfiles.check_destination(link, cfg.data_dir)


# --- a whole push ----------------------------------------------------------------------------------------------------------------

def test_every_file_reaches_its_own_device_intact_and_is_recorded(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png", b"inner a")
    local_file(conn, assets, 1, "Outer", "b.png", b"outer b")
    local_file(conn, assets, 1, "MainLED", "c.png", b"main c")
    local_file(conn, assets, 2, "Inner", "a.png", b"table two a")                    # same name, different table
    result = run(conn, assets, cfg)
    assert (result.pushed, result.failed) == (4, 0)
    assert (devices["t1i"] / "a.png").read_bytes() == b"inner a" and (devices["t2i"] / "a.png").read_bytes() == b"table two a"
    assert (devices["t1o"] / "b.png").read_bytes() == b"outer b" and (devices["t1m"] / "c.png").read_bytes() == b"main c"
    assert all(sorted(p.name for p in d.iterdir()) == sorted(n for n in os.listdir(d) if not n.startswith(".")) for d in devices.values())
    rows = sync_rows(cfg)
    assert [r["status"] for r in rows] == ["Success"] * 4
    assert {r["destination"] for r in rows} == {os.path.join(str(devices["t1i"]), "a.png"), os.path.join(str(devices["t1o"]), "b.png"),
                                                os.path.join(str(devices["t1m"]), "c.png"), os.path.join(str(devices["t2i"]), "a.png")}
    assert db_rows(cfg, "SELECT last_sync FROM events")[0]["last_sync"]
    assert db_rows(cfg, "SELECT COUNT(*) AS n FROM operation_log WHERE operation = 'Synchronise' AND status = 'Success'")[0]["n"] == 4
    assert {m.status for m in mappings.list_mappings(conn, "1000")} == {mappings.STATUS_OK}          # devices were tested first


def test_a_second_run_sends_nothing(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    run(conn, assets, cfg)
    again = run(conn, assets, cfg)
    assert again.total == 0 and len(sync_rows(cfg)) == 1


def test_a_device_that_cannot_be_reached_only_fails_its_own_files(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    local_file(conn, assets, 1, "Outer", "b.png")
    os.rmdir(devices["t1o"])                                                        # the Outer device is gone
    result = run(conn, assets, cfg)
    assert (result.pushed, result.failed) == (1, 1) and result.unreachable == ["Table 1 Outer"]
    assert (devices["t1i"] / "a.png").exists()
    assert [(r["file_name"], r["status"]) for r in sync_rows(cfg)] == [("b.png", "Failure"), ("a.png", "Success")] or \
        sorted((r["file_name"], r["status"]) for r in sync_rows(cfg)) == [("a.png", "Success"), ("b.png", "Failure")]
    assert [m.status for m in mappings.list_mappings(conn, "1000") if m.table_number == 1 and m.led_type == "Outer"] == ["Connection Failed"]
    ex = db_rows(cfg, "SELECT category, operation FROM exception_log")
    assert ex and ex[0]["operation"] == "Test Connection"                              # one exception for the dead device, not one per file
    devices["t1o"].mkdir()                                                            # it comes back
    assert run(conn, assets, cfg).pushed == 1 and (devices["t1o"] / "b.png").exists()


def test_a_transient_failure_is_retried_and_succeeds(world, cfg, monkeypatch):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    real, state = localfiles.push_file, {"n": 0}

    def flaky(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise localfiles.DeviceError(exceptions.NETWORK_DEVICE, "The device could not be reached.")
        return real(*a, **k)
    monkeypatch.setattr(localfiles, "push_file", flaky)
    assert run(conn, assets, cfg).pushed == 1 and state["n"] == 2


def test_after_a_network_failure_the_rest_of_that_device_is_not_attempted_but_other_devices_carry_on(world, cfg, monkeypatch):
    conn, devices, assets = world
    for n in ("a.png", "b.png", "c.png"):
        local_file(conn, assets, 1, "Inner", n)
    local_file(conn, assets, 1, "Outer", "o.png")
    real, calls = localfiles.push_file, []

    def dying(source, dest_dir, name, **k):
        calls.append(name)
        if str(dest_dir) == str(devices["t1i"]):
            raise localfiles.DeviceError(exceptions.NETWORK_DEVICE, "The device could not be reached.")
        return real(source, dest_dir, name, **k)
    monkeypatch.setattr(localfiles, "push_file", dying)
    result = run(conn, assets, cfg)
    assert result.pushed == 1 and result.failed == 3
    assert calls.count("a.png") == 2 and "b.png" not in calls and "c.png" not in calls           # a.png tried twice, then the device is left alone
    msgs = [r["error_message"] for r in sync_rows(cfg) if r["status"] == "Failure"]
    assert msgs.count("Not sent: an earlier file to this device failed.") == 2
    assert (devices["t1o"] / "o.png").exists()


def test_a_copy_that_does_not_verify_is_retried_once_then_reported(world, cfg, monkeypatch):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    calls = []

    def bad(*a, **k):
        calls.append(1)
        raise localfiles.IntegrityError("The copy on the device did not match the original (checksum), so it was discarded.")
    monkeypatch.setattr(localfiles, "push_file", bad)
    result = run(conn, assets, cfg)
    assert result.failed == 1 and len(calls) == 2 and "checksum" in result.failures[0]
    ex = db_rows(cfg, "SELECT category FROM exception_log")
    assert [e["category"] for e in ex] == ["Synchronisation"]


def test_a_file_the_cloud_removed_is_removed_from_the_device_but_nothing_else_is(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "ours.png", b"ours")
    run(conn, assets, cfg)
    (devices["t1i"] / "theirs.png").write_bytes(b"not ours")
    local_file(conn, assets, 1, "Inner", "ours.png", status="Deleted", downloaded_at="2026-09-21T10:00:00Z")
    result = run(conn, assets, cfg)
    assert (result.removed, result.failed) == (1, 0)
    assert not (devices["t1i"] / "ours.png").exists() and (devices["t1i"] / "theirs.png").read_bytes() == b"not ours"
    assert [r["status"] for r in sync_rows(cfg)] == ["Success", "Deleted"]
    assert run(conn, assets, cfg).total == 0


def test_removing_a_file_that_is_already_gone_from_the_device_is_fine(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "ours.png")
    run(conn, assets, cfg)
    (devices["t1i"] / "ours.png").unlink()
    local_file(conn, assets, 1, "Inner", "ours.png", status="Deleted", downloaded_at="2026-09-21T10:00:00Z")
    assert run(conn, assets, cfg).removed == 1


def test_a_device_folder_that_is_the_data_folder_is_refused_before_anything_is_written(world, cfg, tmp_path_factory):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    conn.execute("UPDATE led_mappings SET shared_folder = ? WHERE table_number = 1 AND led_type = 'Inner'", (str(cfg.data_dir),))
    conn.commit()
    result = run(conn, assets, cfg)
    assert result.failed == 1 and result.unreachable == ["Table 1 Inner"]
    assert not (cfg.data_dir / "a.png").exists()


def test_cancelling_between_files_counts_the_rest_and_leaves_devices_clean(world, cfg):
    conn, devices, assets = world
    for n in ("a.png", "b.png", "c.png"):
        local_file(conn, assets, 1, "Inner", n)
    progress = Progress()
    original = progress.finish_file
    progress.finish_file = lambda ok: (original(ok), progress.cancel())
    result = run(conn, assets, cfg, progress=progress)
    assert result.cancelled and result.pushed == 1 and result.remaining == 2
    assert not [p for p in devices["t1i"].iterdir() if p.name.startswith(".")]


def test_progress_counts_what_was_synchronised(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    local_file(conn, assets, 1, "Outer", "b.png")
    progress = Progress()
    progress.set_phase("Synchronising", 2)
    run(conn, assets, cfg, progress=progress)
    snap = progress.snapshot()
    assert (snap["synchronised"], snap["done"], snap["errors"]) == (2, 2, 0)


def test_a_missing_local_copy_fails_that_file_only(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "gone.png")
    local_file(conn, assets, 1, "Inner", "here.png")
    (assets / "Table 1" / "Inner" / "gone.png").unlink()
    result = run(conn, assets, cfg)
    assert (result.pushed, result.failed) == (1, 1) and "missing from this computer" in result.failures[0]


# --- event status ----------------------------------------------------------------------------------------------------------------

def test_the_status_follows_the_real_state(world, cfg):
    conn, devices, assets = world
    assert eventstatus.refresh(conn, "1000") == "Registered"                     # mapped but never tested
    for m in mappings.list_mappings(conn, "1000"):
        mappings.record_test(conn, m, True, "ok", None)
    assert eventstatus.refresh(conn, "1000") == "Ready"
    local_file(conn, assets, 1, "Inner", "a.png")
    assert eventstatus.refresh(conn, "1000") == "Ready"                          # downloaded but waiting to be sent
    run(conn, assets, cfg)
    assert eventstatus.refresh(conn, "1000") == "Synced"
    assert db_rows(cfg, "SELECT status FROM events")[0]["status"] == "Synced"
    local_file(conn, assets, 1, "Inner", "new.png")                              # something new is waiting
    assert eventstatus.refresh(conn, "1000") == "Ready"


def test_a_failed_device_test_or_push_or_download_means_attention_needed_until_resolved(world, cfg):
    conn, devices, assets = world
    for m in mappings.list_mappings(conn, "1000"):
        mappings.record_test(conn, m, True, "ok", None)
    m = mappings.list_mappings(conn, "1000")[0]
    mappings.record_test(conn, m, False, "down", exceptions.NETWORK_DEVICE)
    assert eventstatus.refresh(conn, "1000") == "Attention needed"
    mappings.record_test(conn, mappings.list_mappings(conn, "1000")[0], True, "ok", None)
    assert eventstatus.refresh(conn, "1000") == "Ready"
    conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, status, download_timestamp, source_timestamp) "
                 "VALUES ('1000', 'x.png', 1, 'Inner', 'Failure', '2026-09-20T10:00:00Z', '2026-09-17T06:00:00Z')")
    conn.commit()
    assert eventstatus.refresh(conn, "1000") == "Attention needed"
    local_file(conn, assets, 1, "Inner", "x.png")                                 # a later success resolves it
    run(conn, assets, cfg)
    assert eventstatus.refresh(conn, "1000") == "Synced"
    conn.execute("INSERT INTO sync_history (event_id, file_name, destination, sync_timestamp, status) "
                 "VALUES ('1000', 'x.png', ?, '2026-09-21T10:00:00Z', 'Failure')", (os.path.join(str(devices["t1i"]), "x.png"),))
    conn.commit()
    assert eventstatus.refresh(conn, "1000") == "Attention needed"


def test_refresh_never_raises(world, cfg):
    conn, devices, assets = world
    conn.execute("ALTER TABLE sync_history RENAME TO gone")
    assert eventstatus.refresh(conn, "1000") == ""


# --- the local change log's Sync Status column ---------------------------------------------------------------------------------

def test_the_local_change_log_shows_the_sync_status_of_each_downloaded_file(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "sent.png")
    local_file(conn, assets, 1, "Outer", "waiting.png")
    (devices["t1o"]).rmdir()
    run(conn, assets, cfg)
    local_file(conn, assets, 2, "Inner", "unsent.png")
    localchangelog.write(cfg.data_dir, conn)
    rows = list(csv.reader(open(cfg.data_dir / "_localchangelog.csv", encoding="utf-8-sig", newline="")))
    status = {r[4]: r[10] for r in rows[1:]}
    assert status == {"sent.png": "Success", "waiting.png": "Failure", "unsent.png": ""}


# --- Download & Sync through the app --------------------------------------------------------------------------------------------

@pytest.fixture
def ev(logged_in):
    serve_event(logged_in.application.extensions["test.azure"], dict(REAL_GUID_JSON))
    logged_in.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(logged_in, "/events/new")})
    return logged_in


def azure_of(client):
    return client.application.extensions["test.azure"]


def put(client, path, data=PNG):
    azure_of(client).put("2026", f"{FOLDER}/{path}", data)


def seed(ev):
    put(ev, "Table 1/inner/a.png", b"inner a")
    put(ev, "Table 1/outer/b.png", b"outer b")
    put(ev, "Table 1/mainled/c.png", b"main c")
    put(ev, "Table 2/inner/a.png", b"table two a")
    put(ev, "rpi/HOME_Look.png", b"rpi")
    put(ev, "_ledassetschangelog.csv", csv_text([("Table 1/Inner/a.png", T1, "New"), ("Table 1/Outer/b.png", T1, "New"),
                                                  ("Table 1/MainLED/c.png", T1, "New"), ("Table 2/Inner/a.png", T1, "New"),
                                                  ("RPI/HOME_Look.png", T1, "New")]))


def map_devices(ev, cfg, tmp_path_factory, which=("t1i", "t1o", "t1m", "t2i")):
    devices = {k: tmp_path_factory.mktemp("dev_" + k) for k in ("t1i", "t1o", "t1m", "t2i")}
    conn = connect(cfg.db_path)
    entries = {"t1i": (1, "Inner"), "t1o": (1, "Outer"), "t1m": (1, "MainLED"), "t2i": (2, "Inner")}
    mappings.save_mappings(conn, "1000", real_structure(), {entries[k]: ("", str(devices[k])) for k in which}, forbidden_roots=(cfg.data_dir,))
    conn.close()
    return devices


def tok(client, path="/"):
    return csrf_from(client, path)


def sync_all(client):
    return client.post("/events/1000/sync", data={"csrf_token": tok(client)}, follow_redirects=True)


def text_of(html):
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_download_and_sync_from_the_dashboard_downloads_then_delivers_to_every_device(ev, cfg, tmp_path_factory):
    seed(ev)
    devices = map_devices(ev, cfg, tmp_path_factory)
    html = ev.get("/").get_data(as_text=True)
    assert "Download &amp; Sync" in html and html.count("btn-primary") == 1 and 'action="/events/1000/sync"' in html
    out = text_of(sync_all(ev).get_data(as_text=True))
    assert "Downloaded 5 file(s), removed 0, failed 0." in out and "Synchronised 4 file(s) to the LED devices, removed 0, failed 0." in out
    assert (devices["t1i"] / "a.png").read_bytes() == b"inner a" and (devices["t2i"] / "a.png").read_bytes() == b"table two a"
    assert (devices["t1o"] / "b.png").read_bytes() == b"outer b" and (devices["t1m"] / "c.png").read_bytes() == b"main c"
    assert "Synced" in ev.get("/").get_data(as_text=True)
    assert db_rows(cfg, "SELECT status FROM events")[0]["status"] == "Synced"
    assert {r["status"] for r in sync_rows(cfg)} == {"Success"} and len(sync_rows(cfg)) == 4
    rows = list(csv.reader(open(cfg.data_dir / "_localchangelog.csv", encoding="utf-8-sig", newline="")))
    assert sorted(r[10] for r in rows[1:]) == ["", "Success", "Success", "Success", "Success"]       # the RPI file is not pushed


def test_a_second_download_and_sync_does_nothing(ev, cfg, tmp_path_factory):
    seed(ev)
    map_devices(ev, cfg, tmp_path_factory)
    sync_all(ev)
    n = len(sync_rows(cfg))
    out = text_of(sync_all(ev).get_data(as_text=True))
    assert "Downloaded 0 file(s)" in out and "Synchronised 0 file(s)" in out and len(sync_rows(cfg)) == n


def test_a_dead_device_is_reported_the_others_get_their_files_and_the_status_needs_attention(ev, cfg, tmp_path_factory):
    seed(ev)
    devices = map_devices(ev, cfg, tmp_path_factory)
    os.rmdir(devices["t1o"])
    out = text_of(sync_all(ev).get_data(as_text=True))
    assert "Synchronised 3 file(s)" in out and "failed 1" in out and "Could not use the device folder for: Table 1 Outer" in out
    assert (devices["t1i"] / "a.png").exists() and not devices["t1o"].exists()
    assert db_rows(cfg, "SELECT status FROM events")[0]["status"] == "Attention needed"
    devices["t1o"].mkdir()
    assert "Synchronised 1 file(s)" in text_of(sync_all(ev).get_data(as_text=True))
    assert db_rows(cfg, "SELECT status FROM events")[0]["status"] == "Synced"


def test_an_led_without_a_device_folder_is_named_and_its_files_stay_downloaded(ev, cfg, tmp_path_factory):
    seed(ev)
    devices = map_devices(ev, cfg, tmp_path_factory, which=("t1i", "t1o", "t2i"))
    out = text_of(sync_all(ev).get_data(as_text=True))
    assert "No device folder is set for: Table 1 Main LED" in out
    assert (cfg.data_dir / "Events" / "1000" / "Table 1" / "Main LED" / "c.png").exists()


def test_a_download_that_stops_early_does_not_start_the_sync(ev, cfg, tmp_path_factory, monkeypatch):
    seed(ev)
    map_devices(ev, cfg, tmp_path_factory)
    from ledsync.services import assets, rpi
    monkeypatch.setattr(rpi, "process", lambda *a, **k: transfer.TransferResult(failed=1, stopped=True, failures=["x: lost"]))
    monkeypatch.setattr(assets, "process", lambda *a, **k: transfer.TransferResult())
    out = text_of(sync_all(ev).get_data(as_text=True))
    assert "Synchronisation was not started" in out and sync_rows(cfg) == []


def test_pushing_only_needs_no_cloud_settings_and_no_azure(ev, cfg, tmp_path_factory, monkeypatch):
    seed(ev)
    devices = map_devices(ev, cfg, tmp_path_factory)
    ev.post("/events/1000/changes/check", data={"csrf_token": tok(ev, "/events/1000/changes")})
    ev.post("/events/1000/changes/download", data={"csrf_token": tok(ev, "/events/1000/changes")})
    html = ev.get("/events/1000/changes").get_data(as_text=True)
    assert "Sync To LED Devices (4)" in html
    for name in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY"):
        monkeypatch.delenv(name)
    calls = len(azure_of(ev).calls)
    out = ev.post("/events/1000/sync/push", data={"csrf_token": tok(ev, "/events/1000/changes")}, follow_redirects=True).get_data(as_text=True)
    assert "Synchronised 4 file(s)" in text_of(out) and len(azure_of(ev).calls) == calls
    assert (devices["t1i"] / "a.png").exists() and "Nothing is waiting to be sent to the LED devices." in ev.get("/events/1000/changes").get_data(as_text=True)


def test_download_and_sync_without_cloud_settings_says_so(ev, monkeypatch):
    for name in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY"):
        monkeypatch.delenv(name)
    assert "Cloud storage is not set up yet" in sync_all(ev).get_data(as_text=True)


def test_saving_a_mapping_and_testing_a_device_keep_the_status_up_to_date(ev, cfg, tmp_path_factory):
    devices = {k: tmp_path_factory.mktemp("d" + k) for k in ("a", "b", "c", "d")}
    form = {"csrf_token": tok(ev, "/events/1000"), "action": "test-all",
            "folder-1-Inner": str(devices["a"]), "folder-1-Outer": str(devices["b"]),
            "folder-1-MainLED": str(devices["c"]), "folder-2-Inner": str(devices["d"])}
    ev.post("/events/1000/mappings", data=form)
    assert db_rows(cfg, "SELECT status FROM events")[0]["status"] == "Ready"
    devices["a"].rmdir()
    ev.post("/events/1000/mappings", data={**form, "csrf_token": tok(ev, "/events/1000")})
    assert db_rows(cfg, "SELECT status FROM events")[0]["status"] == "Attention needed"


# --- the dashboard's Operation Status ----------------------------------------------------------------------------------------------

def test_the_operations_endpoint_needs_login_and_shows_running_jobs_with_counters(ev, client, launched):
    assert client.get("/operations/progress").status_code == 403
    assert ev.get("/operations/progress").get_json() == {"jobs": []}
    registry = ev.application.extensions["ledsync.jobs"]
    gate = threading.Event()

    def slow(progress):
        progress.set_phase("Synchronising", 4)
        progress.identify(9)
        progress.tally_downloaded()
        progress.tally_synchronised()
        progress.tally_error()
        progress.begin("v.mp4", 100)
        progress.add_bytes(50)
        gate.wait(10)
        return [{"level": "success", "text": "ok"}]
    job = registry.start("1000", slow, kind="both")
    try:
        jobs = ev.get("/operations/progress").get_json()["jobs"]
        assert len(jobs) == 1 and (jobs[0]["event_id"], jobs[0]["kind"], jobs[0]["phase"], jobs[0]["percent"]) == ("1000", "both", "Synchronising", 50)
        assert (jobs[0]["identified"], jobs[0]["downloaded"], jobs[0]["synchronised"], jobs[0]["errors"]) == (9, 1, 1, 1)
        html = ev.get("/").get_data(as_text=True)
        assert "Event 1000" in html and "Identified 9" in html and "Synchronised 1" in html and "js/dashboard.js" in html
        assert 'action="/events/1000/sync/cancel"' in html and "<noscript>" in html
    finally:
        gate.set()
        job.thread.join(5)
    assert ev.get("/operations/progress").get_json() == {"jobs": []}
    assert "Event 1000: ok" in text_of(ev.get("/").get_data(as_text=True))                           # the summary appears once
    assert "Event 1000: ok" not in text_of(ev.get("/").get_data(as_text=True))


def test_the_dashboard_routes_need_login_the_launch_cookie_and_csrf(ev, client, launched):
    for path in ("/events/1000/sync", "/events/1000/sync/push", "/events/1000/sync/cancel"):
        assert ev.post(path).status_code == 403
        assert client.post(path).status_code == 403
    assert ev.post("/events/9999/sync", data={"csrf_token": tok(ev)}).status_code == 404


def test_a_second_start_while_running_is_refused(ev, cfg, tmp_path_factory):
    registry = ev.application.extensions["ledsync.jobs"]
    gate = threading.Event()
    job = registry.start("1000", lambda p: (gate.wait(10), [])[1])
    try:
        assert "already in progress" in text_of(sync_all(ev).get_data(as_text=True))
    finally:
        gate.set()
        job.thread.join(5)


def test_cancel_from_the_dashboard_stops_the_running_job(ev):
    registry = ev.application.extensions["ledsync.jobs"]
    gate = threading.Event()
    job = registry.start("1000", lambda p: (gate.wait(10), [])[1])
    try:
        ev.post("/events/1000/sync/cancel", data={"csrf_token": tok(ev)})
        assert job.progress.cancelled
    finally:
        gate.set()
        job.thread.join(5)


def test_the_change_log_page_shows_the_sync_section_and_unmapped_leds(ev, cfg, tmp_path_factory):
    seed(ev)
    map_devices(ev, cfg, tmp_path_factory, which=("t1i",))
    ev.post("/events/1000/changes/check", data={"csrf_token": tok(ev, "/events/1000/changes")})
    ev.post("/events/1000/changes/download", data={"csrf_token": tok(ev, "/events/1000/changes")})
    html = ev.get("/events/1000/changes").get_data(as_text=True)
    assert "Sync To LED Devices (1)" in html and "No device folder is set for: Table 1 Main LED, Table 1 Outer, Table 2 Inner" in text_of(html)
    assert html.count("btn-primary") <= 1


def test_only_reads_azure_and_never_writes_outside_the_chosen_folders(ev, cfg, tmp_path_factory):
    seed(ev)
    devices = map_devices(ev, cfg, tmp_path_factory)
    sync_all(ev)
    assert set(azure_of(ev).calls) <= {"list_containers", "list", "download"}
    written = {p for p in cfg.data_dir.rglob("*") if p.is_file()}
    assert not [p for p in written if p.suffix == ".ledsync-tmp"]
    for d in devices.values():
        assert not [p for p in d.iterdir() if p.name.startswith(".")]
