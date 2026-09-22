"""Phase 9 - the exception log and the operational log: resolution, querying, the Logs page, review, export, and the
full login-to-sync cycle (Steps 9.1, 9.2)."""

import csv
import io
import os
import re
from datetime import timedelta, timezone

import pytest
from conftest import csrf_from, db_rows, do_login
from phase6_helpers import real_structure

from ledsync.db import connect, init_db
from ledsync.services import exceptions, mappings, oplog
from test_phase8_sync import ev, local_file, map_devices, no_retry_pause, run, seed, sync_all, text_of, tok, world  # noqa: F401 - fixtures


@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    yield c
    c.close()


def add_exc(conn, *, event="1000", op="Synchronise", cat=exceptions.SYNCHRONISATION, msg="failed", ts="2026-09-21T10:00:00Z", **kw):
    exceptions.record(conn, cat, op, msg, event_id=event, **kw)
    conn.execute("UPDATE exception_log SET timestamp = ? WHERE exception_id = (SELECT MAX(exception_id) FROM exception_log)", (ts,))
    conn.commit()


def status_of(cfg, exception_id=None):
    rows = db_rows(cfg, "SELECT exception_id, resolution_status FROM exception_log ORDER BY exception_id")
    return [r["resolution_status"] for r in rows]


# --- Step 9.1: the exception log -----------------------------------------------------------------------------------------------

def test_every_brd_category_can_be_logged_and_an_unknown_one_is_refused(conn):
    for cat in exceptions.CATEGORIES:
        exceptions.record(conn, cat, "Op", "m", event_id="1000")
    assert len(exceptions.CATEGORIES) == 10 and conn.execute("SELECT COUNT(DISTINCT category) FROM exception_log").fetchone()[0] == 10
    with pytest.raises(ValueError):
        exceptions.record(conn, "Made up", "Op", "m")


def test_resolution_matches_exactly_the_same_kind_of_failure(conn):
    add_exc(conn, file_name="a.png", table_number=1, led_type="Inner", destination="C:\\d")
    add_exc(conn, file_name="b.png", table_number=1, led_type="Inner", destination="C:\\d")
    add_exc(conn, file_name="a.png", table_number=1, led_type="Outer", destination="C:\\d")
    add_exc(conn, event="2000", file_name="a.png", table_number=1, led_type="Inner", destination="C:\\d")
    add_exc(conn, op="Other", file_name="a.png", table_number=1, led_type="Inner", destination="C:\\d")
    add_exc(conn, file_name="a.png", table_number=1, led_type="Inner", destination="C:\\d")           # a repeat of the first
    changed = exceptions.resolve_matching(conn, "1000", "synchronise", table_number=1, led_type="inner", file_name="A.PNG",
                                          destination="c:\\D")
    conn.commit()
    rows = conn.execute("SELECT resolution_status FROM exception_log ORDER BY exception_id").fetchall()
    assert changed == 2 and [r[0] for r in rows] == ["Resolved", "Open", "Open", "Open", "Open", "Resolved"]


def test_resolution_leaves_already_resolved_rows_and_needs_every_field_to_agree(conn):
    add_exc(conn, file_name="a.png")
    conn.execute("UPDATE exception_log SET resolution_status = 'Resolved'")
    conn.commit()
    add_exc(conn, file_name="a.png", table_number=1)                     # has a table: a call without a table does not match it
    assert exceptions.resolve_matching(conn, "1000", "Synchronise", file_name="a.png") == 0
    assert exceptions.resolve_matching(conn, "1000", "Synchronise", file_name="a.png", table_number=1) == 1


def test_an_acknowledged_exception_is_still_resolved_by_a_later_success(conn):
    add_exc(conn, file_name="a.png")
    exceptions.set_status(conn, 1, exceptions.ACKNOWLEDGED)
    assert exceptions.resolve_matching(conn, "1000", "Synchronise", file_name="a.png") == 1


def test_the_administrator_can_set_a_status_and_it_is_audited(conn, cfg):
    add_exc(conn)
    assert exceptions.set_status(conn, 1, exceptions.RESOLVED) is True
    assert exceptions.set_status(conn, 999, exceptions.RESOLVED) is False
    with pytest.raises(ValueError):
        exceptions.set_status(conn, 1, "Ignored")
    rows = db_rows(cfg, "SELECT resolution_status FROM exception_log")
    audit = db_rows(cfg, "SELECT operation, status, message FROM operation_log WHERE operation = 'Exception Reviewed'")
    assert rows[0]["resolution_status"] == "Resolved" and len(audit) == 1 and "Open to Resolved" in audit[0]["message"]


def test_a_failed_device_test_is_resolved_by_a_later_passing_test(world, cfg):
    conn, devices, assets = world
    m = mappings.list_mappings(conn, "1000")[0]
    mappings.record_test(conn, m, False, "The folder does not exist.", exceptions.MISSING_FOLDER)
    assert status_of(cfg) == ["Open"]
    mappings.record_test(conn, mappings.list_mappings(conn, "1000")[0], True, "OK", None)
    assert status_of(cfg) == ["Resolved"]


def test_a_failed_push_is_resolved_when_the_same_file_reaches_the_same_device(world, cfg):
    conn, devices, assets = world
    local_file(conn, assets, 1, "Inner", "a.png")
    local_file(conn, assets, 1, "Outer", "b.png")
    (assets / "Table 1" / "Inner" / "a.png").unlink()
    (assets / "Table 1" / "Outer" / "b.png").unlink()
    assert run(conn, assets, cfg).failed == 2
    assert status_of(cfg) == ["Open", "Open"]
    (assets / "Table 1" / "Inner" / "a.png").write_bytes(b"x")                           # only a.png is repaired
    assert run(conn, assets, cfg).pushed == 1
    assert sorted(status_of(cfg)) == ["Open", "Open", "Resolved"]                         # b.png failed again; a.png's first is resolved


def test_a_registration_refusal_is_resolved_when_the_event_registers(conn, cfg):
    from ledsync.services import registration as reg
    exceptions.record(conn, exceptions.GUID_VALIDATION, "Register Event", "rejected", event_id="1000")
    exceptions.record(conn, exceptions.GUID_VALIDATION, "Register Event", "rejected", event_id="2000")
    exceptions.record(conn, exceptions.PERMISSION, "Synchronise", "other", event_id="1000")
    reg._resolve_registration_failures(conn, "1000")
    assert status_of(cfg) == ["Resolved", "Open", "Open"]


def test_a_refused_mapping_is_an_invalid_configuration_exception_without_the_typed_value(ev, cfg):
    secret = "sk_live_" + "A1b2C3d4" * 6
    ev.post("/events/1000/mappings", data={"csrf_token": tok(ev, "/events/1000"), "action": "save", "ip-1-Inner": secret})
    rows = db_rows(cfg, "SELECT category, operation, message, event_id FROM exception_log")
    assert [(r["category"], r["operation"], r["event_id"]) for r in rows] == [("Invalid configuration", "Save Device Mapping", "1000")]
    assert secret not in rows[0]["message"] and "'...'" in rows[0]["message"]
    assert secret not in str(db_rows(cfg, "SELECT * FROM operation_log"))
    assert db_rows(cfg, "SELECT status FROM operation_log WHERE operation = 'Mapping Saved'")[-1]["status"] == "Failed"


def test_a_refused_folder_setting_is_logged(ev, cfg):
    ev.post("/settings/folders", data={"csrf_token": tok(ev, "/settings/folders"), "rpi_folder": "relative\\x", "asset_folder": ""})
    rows = db_rows(cfg, "SELECT category, operation FROM exception_log")
    assert rows == [{"category": "Invalid configuration", "operation": "Save Folder Settings"}]


def test_three_different_categories_are_logged_through_the_app_and_correctly_categorised(ev, cfg, tmp_path_factory):
    """Step 9.1 validation: a wrong GUID, an unreachable device folder, and a refused configuration (plus an event that is not in Azure)."""
    from conftest import serve_event
    from phase6_helpers import REAL_GUID_JSON
    gone = tmp_path_factory.mktemp("gone") / "missing"
    serve_event(ev.application.extensions["test.azure"], dict(REAL_GUID_JSON, exportGuid="11111111-2222-4333-8444-555555555555"))
    ev.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(ev, "/events/new")})                # a different GUID
    ev.post("/events/1000/mappings", data={"csrf_token": tok(ev, "/events/1000"), "action": "test-all", "folder-1-Inner": str(gone)})
    ev.post("/events/1000/mappings", data={"csrf_token": tok(ev, "/events/1000"), "action": "save", "folder-1-Outer": "not a path"})
    ev.post("/events/new", data={"event_id": "7777", "csrf_token": csrf_from(ev, "/events/new")})
    pairs = {(r["category"], r["operation"]) for r in db_rows(cfg, "SELECT category, operation FROM exception_log")}
    assert ("GUID validation", "Register Event") in pairs
    assert ("Invalid configuration", "Save Device Mapping") in pairs
    assert any(op == "Test Connection" and cat in ("Missing folder", "Network/device connectivity") for cat, op in pairs)
    assert any(op == "Register Event" and cat != "GUID validation" for cat, op in pairs)
    assert len({cat for cat, _ in pairs}) >= 3
    text = text_of(ev.get("/logs?tab=exceptions").get_data(as_text=True))
    assert "GUID validation" in text and "Invalid configuration" in text and "Test Connection" in text


# --- querying ---------------------------------------------------------------------------------------------------------------------

def test_exception_filters_and_paging(conn):
    add_exc(conn, event="1000", cat=exceptions.DOWNLOAD, msg="a", ts="2026-09-20T10:00:00Z")
    add_exc(conn, event="2000", cat=exceptions.PERMISSION, msg="100% sure_thing", ts="2026-09-21T10:00:00Z", file_name="x.png")
    add_exc(conn, event="1000", cat=exceptions.PERMISSION, msg="c", ts="2026-09-22T10:00:00Z")
    conn.execute("UPDATE exception_log SET resolution_status = 'Resolved' WHERE exception_id = 3")
    conn.commit()
    q = lambda **k: [r["exception_id"] for r in exceptions.query(conn, **k)[0]]                    # noqa: E731
    assert q() == [3, 2, 1]
    assert q(event_id="1000") == [3, 1] and q(event_id="2000") == [2]
    assert q(category="Permission") == [3, 2] and q(category="Nonsense") == [3, 2, 1]
    assert q(status="Resolved") == [3] and q(status="Open") == [2, 1]
    assert q(start="2026-09-21T00:00:00Z", end="2026-09-22T00:00:00Z") == [2]
    assert q(text="100%") == [2] and q(text="sure_thing") == [2] and q(text="%") == [2]            # wildcards are literal text
    assert q(text="x.png") == [2]
    rows, total = exceptions.query(conn, limit=2, offset=1)
    assert total == 3 and [r["exception_id"] for r in rows] == [2, 1]


def test_operational_filters(conn):
    for op, status, msg, ts in (("Login", "Success", "in", "2026-09-20T10:00:00Z"), ("Login", "Failed", "bad", "2026-09-21T10:00:00Z"),
                                ("Synchronise", "Success", "sent a.png", "2026-09-22T10:00:00Z")):
        oplog.record(conn, op, status, msg, "1000")
        conn.execute("UPDATE operation_log SET timestamp = ? WHERE log_id = (SELECT MAX(log_id) FROM operation_log)", (ts,))
    conn.commit()
    q = lambda **k: [r["message"] for r in oplog.query(conn, **k)[0]]                              # noqa: E731
    assert q() == ["sent a.png", "bad", "in"] and q(operation="login") == ["bad", "in"] and q(status="Failed") == ["bad"]
    assert q(text="a.png") == ["sent a.png"] and q(event_id="2000") == []
    assert q(start="2026-09-21T00:00:00Z", end="2026-09-22T00:00:00Z") == ["bad"]


# --- the Logs page ------------------------------------------------------------------------------------------------------------------

def test_the_logs_page_needs_login_and_the_launch_cookie(ev, client, launched):
    for path in ("/logs", "/logs?tab=exceptions", "/logs/export"):
        assert client.get(path).status_code == 403                                     # no launch cookie
        assert ev.post("/logout", data={"csrf_token": tok(ev)}).status_code == 302
        assert ev.get(path).status_code == 302                                         # launched but not signed in: to the login page
        do_login(ev)
    assert ev.get("/logs").status_code == 200 and ev.get("/logs/export").status_code == 200


def test_times_are_shown_as_dd_mm_yy_hh_mm_ss_in_local_time(ev, cfg):
    ev.application.config["DISPLAY_TZ"] = timezone(timedelta(hours=4))
    conn = connect(cfg.db_path)
    oplog.record(conn, "Login", "Success", "timed")
    conn.execute("UPDATE operation_log SET timestamp = '2026-09-21T10:07:22Z' WHERE message = 'timed'")
    add_exc(conn, ts="2026-12-31T21:59:59Z")
    conn.commit()
    conn.close()
    ops = text_of(ev.get("/logs").get_data(as_text=True))
    assert "21/09/26 14:07:22" in ops
    assert "01/01/27 01:59:59" in text_of(ev.get("/logs?tab=exceptions").get_data(as_text=True))


def test_the_page_lists_newest_first_and_escapes_what_it_shows(ev, cfg):
    conn = connect(cfg.db_path)
    oplog.record(conn, "Login", "Success", "<script>alert(1)</script>")
    conn.close()
    html = ev.get("/logs").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    body = html.split("<tbody>")[1]
    assert body.index("&lt;script&gt;") < body.index("Event Registered")


def test_filters_dates_and_paging_work_on_the_page(ev, cfg):
    conn = connect(cfg.db_path)
    for i in range(230):
        oplog.record(conn, "Synchronise", "Success", f"row {i:03d}", "1000")
    conn.close()
    first = text_of(ev.get("/logs?kind=Synchronise").get_data(as_text=True))
    assert "230 records" in first and "page 1 of 3" in first and "row 229" in first and "row 129" not in first
    assert "row 129" in text_of(ev.get("/logs?kind=Synchronise&page=2").get_data(as_text=True))
    page3 = ev.get("/logs?kind=Synchronise&page=3").get_data(as_text=True)
    assert "row 000" in page3 and "Newer" in page3 and "Older" not in page3
    assert "0 records" in text_of(ev.get("/logs?kind=Synchronise&from=2001-01-01&to=2001-01-02").get_data(as_text=True))
    assert "230 records" in text_of(ev.get("/logs?kind=Synchronise&from=2020-01-01&to=2099-01-01").get_data(as_text=True))
    assert "0 records" in text_of(ev.get("/logs?kind=Synchronise&text=nothing-like-this").get_data(as_text=True))
    assert ev.get("/logs?page=abc&from=notadate&tab=zzz").status_code == 200                       # bad input never breaks the page


def test_a_date_filter_means_the_local_day(ev, cfg):
    ev.application.config["DISPLAY_TZ"] = timezone(timedelta(hours=4))
    conn = connect(cfg.db_path)
    for stamp, name in (("2026-09-20T19:59:59Z", "before"), ("2026-09-20T20:00:00Z", "inside"), ("2026-09-21T19:59:59Z", "last"),
                        ("2026-09-21T20:00:00Z", "after")):
        oplog.record(conn, "Synchronise", "Success", name)
        conn.execute("UPDATE operation_log SET timestamp = ? WHERE message = ?", (stamp, name))
    conn.commit()
    conn.close()
    text = text_of(ev.get("/logs?kind=Synchronise&from=2026-09-21&to=2026-09-21").get_data(as_text=True))
    assert "2 records" in text and "inside" in text and "last" in text and "before" not in text and "after" not in text


def test_review_changes_the_status_keeps_the_filter_and_needs_csrf(ev, cfg):
    conn = connect(cfg.db_path)
    add_exc(conn, file_name="a.png")
    conn.close()
    url = "/logs/exceptions/1/status"
    assert ev.post(url, data={"new_status": "Resolved"}).status_code == 403                       # no token
    token = tok(ev, "/logs?tab=exceptions")
    assert ev.post(url, data={"csrf_token": token, "new_status": "Ignored"}).status_code == 400
    assert ev.post("/logs/exceptions/999/status", data={"csrf_token": token, "new_status": "Resolved"}).status_code == 404
    out = ev.post(url, data={"csrf_token": token, "new_status": "Acknowledged", "event": "1000", "kind": "Synchronisation"})
    assert out.status_code == 302 and "tab=exceptions" in out.headers["Location"] and "event=1000" in out.headers["Location"]
    assert status_of(cfg) == ["Acknowledged"]
    page = text_of(ev.get(out.headers["Location"]).get_data(as_text=True))
    assert "Exception 1 marked Acknowledged." in page and "Acknowledged" in page


def test_the_review_control_offers_every_status_and_names_the_exception(ev, cfg):
    conn = connect(cfg.db_path)
    add_exc(conn)
    conn.close()
    html = ev.get("/logs?tab=exceptions").get_data(as_text=True)
    assert 'aria-label="Change status of exception 1"' in html and 'aria-label="Save status of exception 1"' in html
    assert all(f'value="{s}"' in html for s in exceptions.STATUSES) and html.count("btn-primary") == 1


def test_export_downloads_the_filtered_rows_as_safe_csv_and_is_audited(ev, cfg):
    conn = connect(cfg.db_path)
    add_exc(conn, event="1000", cat=exceptions.PERMISSION, msg="=HYPERLINK(\"http://x\")", file_name="a.png", table_number=1,
            led_type="Inner", destination="C:\\d")
    add_exc(conn, event="2000", cat=exceptions.DOWNLOAD, msg="other")
    conn.close()
    out = ev.get("/logs/export?tab=exceptions&kind=Permission")
    assert out.status_code == 200 and out.mimetype == "text/csv" and "no-store" in out.headers["Cache-Control"]
    assert 'filename="ledsync_exception_log.csv"' in out.headers["Content-Disposition"]
    body = out.get_data(as_text=True)
    assert body.startswith(chr(0xFEFF))
    rows = list(csv.reader(io.StringIO(body[1:])))
    assert rows[0] == ["Date/Time", "Event ID", "Table", "LED Type", "File Name", "Operation", "Error Category", "Error Description",
                       "Source", "Destination", "Resolution/Status"]
    assert len(rows) == 2 and rows[1][1] == "1000" and rows[1][7].startswith("'=") and rows[1][10] == "Open"
    assert re.fullmatch(r"\d\d/\d\d/\d\d \d\d:\d\d:\d\d", rows[1][0])
    ops = ev.get("/logs/export").get_data(as_text=True)
    assert ops.splitlines()[0].lstrip(chr(0xFEFF)) == "Date/Time,Event ID,Operation,Status,Message" and "Log Export" in ops
    assert db_rows(cfg, "SELECT COUNT(*) AS n FROM operation_log WHERE operation = 'Log Export'")[0]["n"] == 2


def test_the_topbar_links_to_the_logs_and_the_page_has_one_primary_action(ev):
    assert 'href="/logs"' in ev.get("/").get_data(as_text=True)
    html = ev.get("/logs").get_data(as_text=True)
    assert html.count("btn-primary") == 1 and 'aria-current="page"' in html and "Operational Log" in html and "Exception Log" in html


# --- Step 9.2: the full login-to-sync cycle ----------------------------------------------------------------------------------------

def test_a_full_login_to_sync_cycle_puts_every_kind_of_event_in_the_operational_log_with_correct_times(ev, cfg, tmp_path_factory):
    from datetime import datetime
    from ledsync.main import _seed_and_log_startup
    before = datetime.now(timezone.utc).replace(microsecond=0)
    _seed_and_log_startup(cfg)                                                             # application startup
    seed(ev)
    devices = map_devices(ev, cfg, tmp_path_factory)                                       # mapping saved
    ev.post("/events/1000/mappings", data={"csrf_token": tok(ev, "/events/1000"), "action": "test-all",
                                           **{f"folder-{t}-{l}": str(devices[k]) for k, (t, l) in
                                              {"t1i": (1, "Inner"), "t1o": (1, "Outer"), "t1m": (1, "MainLED"), "t2i": (2, "Inner")}.items()}})
    ev.post("/settings/folders", data={"csrf_token": tok(ev, "/settings/folders"), "rpi_folder": "", "asset_folder": ""})
    ev.post("/settings/cloud", data={"csrf_token": tok(ev, "/settings/cloud"), "account": "sasportspresentation", "container": "2026",
                                     "access_key": "", "action": "test"})
    sync_all(ev)
    ev.post("/account/password", data={"csrf_token": tok(ev, "/account/password"), "current_password": "wrong",
                                       "new_password": "Another@123", "confirm_password": "Another@123"})
    ev.post("/logout", data={"csrf_token": tok(ev)})
    after = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
    do_login(ev, password="nope")                                                           # a failed login (the launch cookie stays)
    rows = db_rows(cfg, "SELECT operation, status, timestamp, message FROM operation_log ORDER BY log_id")
    seen = {(r["operation"], r["status"]) for r in rows}
    wanted = {("Application Startup", "Success"), ("Login", "Success"), ("Event Registered", "Success"), ("Mapping Saved", "Success"),
              ("Device Test", "Success"), ("Settings Changed", "Success"), ("Cloud Storage Test", "Success"),
              ("Check Changes", "Success"), ("Download & Sync", "Started"), ("Download & Sync", "Success"),
              ("Asset Download", "Success"), ("RPI Files", "Success"), ("Synchronise", "Success"),
              ("Password Change", "Failed"), ("Logout", "Success")}
    assert wanted <= seen, wanted - seen
    for r in rows:
        stamp = datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        assert before - timedelta(seconds=5) <= stamp <= after + timedelta(seconds=5), r
    order = [(r["operation"], r["status"]) for r in rows]
    assert order.index(("Download & Sync", "Started")) < order.index(("Asset Download", "Success")) < order.index(("Download & Sync", "Success"))
    finish = [r for r in rows if r["operation"] == "Download & Sync" and r["status"] == "Success"][0]["message"]
    assert finish == "Identified 79, downloaded 5, synchronised 4, errors 0." or re.fullmatch(
        r"Identified \d+, downloaded 5, synchronised 4, errors 0\.", finish), finish
    assert not [r for r in rows if "Admin@123" in r["message"] or "Another@123" in r["message"]]
    assert ("Login", "Failed") in {(r["operation"], r["status"]) for r in db_rows(cfg, "SELECT operation, status FROM operation_log")}
    do_login(ev)
    page = text_of(ev.get("/logs?kind=Download+%26+Sync").get_data(as_text=True))
    assert "Login" in page and "Download &amp; Sync" in page or "Download & Sync" in page


def test_a_failed_run_is_recorded_as_failed_with_its_counts(ev, cfg, tmp_path_factory):
    seed(ev)
    devices = map_devices(ev, cfg, tmp_path_factory)
    os.rmdir(devices["t1o"])
    sync_all(ev)
    finish = db_rows(cfg, "SELECT status, message FROM operation_log WHERE operation = 'Download & Sync' ORDER BY log_id")
    assert [r["status"] for r in finish] == ["Started", "Failed"] and "errors 1" in finish[1]["message"] and "synchronised 3" in finish[1]["message"]


def test_a_push_only_run_and_a_download_only_run_have_their_own_names(ev, cfg, tmp_path_factory):
    seed(ev)
    map_devices(ev, cfg, tmp_path_factory)
    ev.post("/events/1000/changes/check", data={"csrf_token": tok(ev, "/events/1000/changes")})
    ev.post("/events/1000/changes/download", data={"csrf_token": tok(ev, "/events/1000/changes")})
    ev.post("/events/1000/sync/push", data={"csrf_token": tok(ev, "/events/1000/changes")})
    ops = [(r["operation"], r["status"]) for r in db_rows(cfg, "SELECT operation, status FROM operation_log WHERE operation IN "
                                                              "('Download Files', 'Sync Files') ORDER BY log_id")]
    assert ops == [("Download Files", "Started"), ("Download Files", "Success"), ("Sync Files", "Started"), ("Sync Files", "Success")]


def test_the_later_phase_operation_names_are_reserved_in_the_filter(ev):
    html = ev.get("/logs").get_data(as_text=True)
    assert 'value="Scheduled Run"' in html and 'value="Application Update"' in html and 'value="Application Startup"' in html
