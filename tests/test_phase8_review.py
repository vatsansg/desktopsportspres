"""Phase 8 - regression tests for the independent architect review (device-folder overlap, per-device isolation, removals,
event status, stalled copies, dashboard accessibility)."""

import os
import threading
import time

import pytest
from conftest import db_rows
from phase6_helpers import real_structure

from ledsync.services import eventstatus, exceptions, localfiles, mappings, sync
from test_phase8_sync import (PNG, ev, local_file, no_retry_pause, run, tok, world)  # noqa: F401 - fixtures


def test_a_device_folder_that_overlaps_the_local_asset_folder_is_refused_and_no_original_is_touched(world, cfg):
    conn, devices, assets = world                                        # the assets live OUTSIDE the data folder here
    local_file(conn, assets, 1, "Inner", "x.png", b"INNER")
    local_file(conn, assets, 1, "Outer", "x.png", b"OUTER")
    mappings.save_mappings(conn, "1000", real_structure(), {(1, "Inner"): ("", str(assets / "Table 1" / "Outer"))}, forbidden_roots=())
    items, _ = sync.plan(conn, "1000", assets)
    result = sync.process(conn, "1000", items, cfg.data_dir, None, None, (str(assets.parent),))
    assert result.pushed == 1 and result.failed == 1 and result.unreachable == ["Table 1 Inner"]
    assert (assets / "Table 1" / "Outer" / "x.png").read_bytes() == b"OUTER"
    with pytest.raises(localfiles.DeviceError):
        localfiles.check_destination(assets / "Table 1", cfg.data_dir, (str(assets),))                  # inside the assets
    with pytest.raises(localfiles.DeviceError):
        localfiles.check_destination(assets, cfg.data_dir, (str(assets),))                              # the assets themselves
    with pytest.raises(localfiles.DeviceError):
        localfiles.check_destination(assets.parent, cfg.data_dir, (str(assets),))                       # a folder that CONTAINS them


def test_saving_a_device_folder_inside_the_asset_folder_is_refused_in_the_page(ev, cfg):
    inside = cfg.data_dir / "Events" / "1000" / "Table 1" / "Inner"
    inside.mkdir(parents=True)
    out = ev.post("/events/1000/mappings", data={"csrf_token": tok(ev, "/events/1000"), "action": "save", "folder-1-Inner": str(inside)})
    assert out.status_code == 400
    assert db_rows(cfg, "SELECT shared_folder FROM led_mappings WHERE led_type = 'Inner' AND table_number = 1")[0]["shared_folder"] == ""


def test_a_device_check_that_times_out_only_fails_that_device(world, cfg, monkeypatch):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    local_file(conn, assets, 1, "Outer", "b.png")
    real = localfiles._unsafe_root

    def slow(root, real_path, data_dir):
        if os.path.samefile(root, devices["t1i"]):
            time.sleep(1.0)
        return real(root, real_path, data_dir)
    monkeypatch.setattr(localfiles, "_unsafe_root", slow)
    monkeypatch.setattr(localfiles, "FOLDER_TIMEOUT", 0.2)
    result = run(conn, assets, cfg)
    assert result.pushed == 1 and result.failed == 1 and result.unreachable == ["Table 1 Inner"]
    assert (devices["t1o"] / "b.png").exists() and not (devices["t1i"] / "a.png").exists()


def test_a_failed_removal_is_planned_again_and_the_status_recovers(world, cfg, monkeypatch):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    run(conn, assets, cfg)
    local_file(conn, assets, 1, "Inner", "a.png", status="Deleted", downloaded_at="2026-09-21T10:00:00Z")
    real = localfiles.delete_file

    def refuse(*a, **k):
        raise localfiles.DeviceError(exceptions.PERMISSION, "The file could not be removed.")
    monkeypatch.setattr(localfiles, "delete_file", refuse)
    assert run(conn, assets, cfg).failed == 1
    assert eventstatus.refresh(conn, "1000") == "Attention needed" and (devices["t1i"] / "a.png").exists()
    assert [(i.file_name, i.action) for i in sync.plan(conn, "1000", assets)[0]] == [("a.png", "Remove")]
    monkeypatch.setattr(localfiles, "delete_file", real)
    assert run(conn, assets, cfg).removed == 1 and not (devices["t1i"] / "a.png").exists()
    for m in mappings.list_mappings(conn, "1000"):
        mappings.record_test(conn, m, True, "ok", None)
    assert eventstatus.refresh(conn, "1000") == "Synced"


def test_a_file_pushed_once_whose_later_push_failed_is_still_removed_when_the_cloud_removes_it(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    run(conn, assets, cfg)
    local_file(conn, assets, 1, "Inner", "a.png", downloaded_at="2099-01-01T00:00:00Z")            # downloaded again ...
    (assets / "Table 1" / "Inner" / "a.png").unlink()                                               # ... but the push fails
    assert run(conn, assets, cfg).failed == 1
    local_file(conn, assets, 1, "Inner", "a.png", status="Deleted", downloaded_at="2026-09-21T10:00:00Z")
    assert [i.action for i in sync.plan(conn, "1000", assets)[0]] == ["Remove"]


def test_the_status_recovers_after_the_device_folder_is_changed_following_a_failure(world, cfg, tmp_path_factory):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    (assets / "Table 1" / "Inner" / "a.png").unlink()
    run(conn, assets, cfg)
    assert eventstatus.refresh(conn, "1000") == "Attention needed"
    (assets / "Table 1" / "Inner" / "a.png").write_bytes(PNG)
    new = tmp_path_factory.mktemp("newdev")
    mappings.save_mappings(conn, "1000", real_structure(), {(1, "Inner"): ("", str(new))}, forbidden_roots=(cfg.data_dir,))
    assert run(conn, assets, cfg).pushed == 1
    for m in mappings.list_mappings(conn, "1000"):
        mappings.record_test(conn, m, True, "ok", None)
    assert eventstatus.refresh(conn, "1000") == "Synced"


def test_a_folder_shared_with_another_mapping_is_never_cleaned_by_a_removal(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    run(conn, assets, cfg)
    conn.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('2000', 'y', 'g2')")
    conn.execute("INSERT INTO led_mappings (event_id, table_number, led_type, shared_folder, enabled) VALUES ('2000', 1, 'Inner', ?, 1)",
                 (str(devices["t1i"]),))
    conn.commit()
    local_file(conn, assets, 1, "Inner", "a.png", status="Deleted", downloaded_at="2026-09-21T10:00:00Z")
    assert sync.plan(conn, "1000", assets)[0] == [] and (devices["t1i"] / "a.png").exists()


def test_the_status_is_not_synced_while_an_led_with_files_has_no_device_folder(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    local_file(conn, assets, 1, "Outer", "b.png")
    mappings.save_mappings(conn, "1000", real_structure(), {(1, "Outer"): ("", "")}, forbidden_roots=())
    run(conn, assets, cfg)
    assert eventstatus.refresh(conn, "1000") != "Synced"


def test_a_download_of_only_rpi_files_does_not_make_the_event_synced(world, cfg):
    conn, devices, assets = world
    conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, status, download_timestamp, source_timestamp) "
                 "VALUES ('1000', 'rpi.png', NULL, 'RPI', 'Success', '2026-09-20T10:00:00Z', '2026-09-17T06:00:00Z')")
    conn.commit()
    assert eventstatus.refresh(conn, "1000") != "Synced"


def test_a_copy_that_stops_responding_is_abandoned_and_cleans_up_after_itself(tmp_path_factory, monkeypatch):
    src_dir, dev = tmp_path_factory.mktemp("s"), tmp_path_factory.mktemp("d")
    (src_dir / "a.png").write_bytes(PNG)
    real_open = open

    class Stalling:
        def __init__(self, handle):
            self.h = handle

        def __enter__(self):
            self.h.__enter__()
            return self

        def __exit__(self, *a):
            return self.h.__exit__(*a)

        def write(self, data):
            time.sleep(1.2)                                                     # the share stops answering
            return self.h.write(data)

        def __getattr__(self, name):
            return getattr(self.h, name)

    def stalling_open(path, mode="r", *a, **k):
        handle = real_open(path, mode, *a, **k)
        return Stalling(handle) if "x" in mode and str(path).endswith(".ledsync-tmp") else handle
    monkeypatch.setattr(localfiles, "open", stalling_open, raising=False)
    monkeypatch.setattr(localfiles, "STALL_SECONDS", 0.3)
    started = time.monotonic()
    with pytest.raises(localfiles.DeviceError) as err:
        localfiles.push_file(src_dir / "a.png", dev, "a.png")
    assert err.value.category == exceptions.NETWORK_DEVICE and time.monotonic() - started < 1.0
    time.sleep(1.8)                                                              # the abandoned copy wakes up and tidies its temp file
    assert list(dev.iterdir()) == []


def test_the_dashboard_panel_announces_only_the_status_line_and_the_button_names_match(ev):
    registry = ev.application.extensions["ledsync.jobs"]
    gate = threading.Event()
    job = registry.start("1000", lambda p: (gate.wait(10), [])[1])
    try:
        html = ev.get("/").get_data(as_text=True)
    finally:
        gate.set()
        job.thread.join(5)
    assert 'class="op-text" role="status" aria-live="polite"' in html and html.count("aria-live") == 1
    assert 'aria-label="Download &amp; Sync event 1000"' in html
