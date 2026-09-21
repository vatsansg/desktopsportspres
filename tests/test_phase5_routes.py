"""Phase 5 - Event Details page: LED structure, device mapping, connection tests (Steps 5.1-5.3)."""

import json
import re
from pathlib import Path

import pytest
from conftest import csrf_from, db_rows, serve_event

from ledsync.services import connectivity

ROOT = Path(__file__).resolve().parent.parent
REAL = json.loads((ROOT / "reference-docs" / "samplefiles-webassetmgmt" / "_GUID.json").read_text(encoding="utf-8"))
NEW_GUID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def register(client, obj=None):
    obj = dict(REAL if obj is None else obj)
    serve_event(client.application.extensions["test.azure"], obj)
    resp = client.post("/events/new", data={"event_id": str(obj["eventId"]),
                                            "csrf_token": csrf_from(client, "/events/new")})
    assert resp.status_code == 302, resp.get_data(as_text=True)[:400]


@pytest.fixture
def ev(logged_in):
    register(logged_in)
    return logged_in


@pytest.fixture
def shares(tmp_path_factory):
    """Where the 'device shared folders' live: NOT inside the application data directory (that is refused)."""
    return tmp_path_factory.mktemp("shares")


def page(client, event_id="1000"):
    return client.get(f"/events/{event_id}").get_data(as_text=True)


def post(client, data, event_id="1000", *, follow=False):
    form = {"csrf_token": csrf_from(client, f"/events/{event_id}"), **data}
    return client.post(f"/events/{event_id}/mappings", data=form, follow_redirects=follow)


def folder(root, name):
    p = root / name
    p.mkdir()
    return str(p)


def mapping_rows(cfg):
    return db_rows(cfg, "SELECT * FROM led_mappings ORDER BY table_number, led_type")


# --- access ------------------------------------------------------------------------------------------------

def test_the_page_needs_login_and_the_launch_cookie(app, client, launched):
    assert client.get("/events/1000").status_code == 403                    # no launch cookie
    assert client.post("/events/1000/mappings", data={"action": "save"}).status_code == 403
    resp = launched.get("/events/1000")                                     # launched but signed out
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    assert launched.post("/events/1000/mappings", data={"action": "save"}).status_code in (302, 403)


def test_posts_without_a_csrf_token_are_refused_and_change_nothing(ev, cfg):
    resp = ev.post("/events/1000/mappings", data={"action": "save", "folder-1-Inner": r"\\d\x"})
    assert resp.status_code == 403
    assert all(r["shared_folder"] == "" for r in mapping_rows(cfg))


def test_unknown_or_malformed_event_ids_get_the_branded_404(ev):
    for bad in ("9999", "abc", "..%2f..", "1000%00", "a" * 200, "new%20"):
        resp = ev.get(f"/events/{bad}")
        assert resp.status_code == 404 and "Page Not Found" in resp.get_data(as_text=True), bad
    assert ev.post("/events/9999/mappings", data={"csrf_token": csrf_from(ev, "/events/1000"), "action": "save"}).status_code == 404


def test_event_ids_are_matched_case_insensitively(logged_in):
    register(logged_in, dict(REAL, eventId="Doha-A", exportGuid=NEW_GUID))
    resp = logged_in.get("/events/doha-a")
    assert resp.status_code == 200 and "Event Doha-A" in resp.get_data(as_text=True)


def test_reserved_event_ids_cannot_be_registered(logged_in):
    for reserved in ("new", "Reregister"):
        serve_event(logged_in.application.extensions["test.azure"], dict(REAL, eventId=reserved), folder=f"{reserved} - x")
        resp = logged_in.post("/events/new", data={"event_id": reserved, "csrf_token": csrf_from(logged_in, "/events/new")})
        assert resp.status_code in (200, 400) and "cannot be used" in resp.get_data(as_text=True)


# --- structure display (5.1) ---------------------------------------------------------------------------------

def test_dashboard_rows_link_to_the_details_page(ev):
    html = ev.get("/").get_data(as_text=True)
    assert html.count('href="/events/1000"') == 2


def test_the_page_shows_the_led_structure_exactly_as_exported(ev):
    html = page(ev)
    assert "Star contender Doha" in html and "LED Configuration" in html
    rows = re.findall(r"<tr><th scope=\"row\">Table (\d+)</th>(.*?)</tr>", html, re.S)
    got = {n: re.findall(r"(Enabled|Not used)", cells) for n, cells in rows}
    assert got == {"1": ["Enabled", "Enabled", "Enabled"], "2": ["Enabled", "Not used", "Not used"]}
    assert ">Main LED</th>" in html


def test_only_enabled_leds_get_mapping_rows(ev):
    html = page(ev)
    for name in ("folder-1-Inner", "folder-1-Outer", "folder-1-MainLED", "folder-2-Inner"):
        assert f'name="{name}"' in html
    for name in ("folder-2-Outer", "folder-2-MainLED"):
        assert name not in html
    assert html.count("Not mapped") == 4


def test_a_table_with_no_led_shows_but_has_no_mapping_row(logged_in):
    register(logged_in, dict(REAL, tables=[{"tableNumber": 5, "innerLed": False, "outerLed": False, "mainLed": False}]))
    html = page(logged_in)
    assert "Table 5" in html and "no enabled LEDs" in html and 'name="folder-5' not in html


# --- saving (5.2) --------------------------------------------------------------------------------------------

def test_save_persists_and_shows_the_values_again(ev, cfg):
    resp = post(ev, {"action": "save", "ip-1-Inner": "10.0.0.5", "folder-1-Inner": r"\\device01\Inner"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/events/1000")
    html = page(ev)
    assert "Device mapping saved." in html
    assert 'value="10.0.0.5"' in html and r'value="\\device01\Inner"' in html
    assert "Not tested" in html
    assert page(ev).count("Device mapping saved.") == 0          # a flash is shown once


def test_a_repeat_save_says_nothing_changed(ev):
    post(ev, {"action": "save", "folder-1-Inner": r"\\d\a"})
    page(ev)
    post(ev, {"action": "save", "folder-1-Inner": r"\\d\a"})
    assert "Nothing changed" in page(ev)


@pytest.mark.parametrize("field,value,fragment", [
    ("ip-1-Inner", "999.1.1.1", "Table 1 Inner"),
    ("folder-1-Inner", "relative", "full folder path"),
    ("folder-1-Inner", r"\\d\a\..\b", "may not contain empty"),
    ("folder-1-Inner", "C:\\Windows", "reserved"),
    ("folder-1-Inner", r"\\d\a:stream", "colon"),
    ("folder-1-Inner", "http://evil/x", "full folder path"),
], ids=lambda v: v[:14])
def test_a_rejected_form_shows_the_reason_keeps_what_was_typed_and_saves_nothing(ev, cfg, field, value, fragment):
    resp = post(ev, {"action": "save", "folder-1-Outer": r"\\d\fine", field: value})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and fragment in html
    assert 'role="alert"' in html and "data-focus-alert" in html
    assert r"\\d\fine" in html                                   # the good row's text is not lost
    assert all(r["shared_folder"] == "" for r in mapping_rows(cfg))


def test_a_duplicate_folder_is_rejected(ev, cfg):
    resp = post(ev, {"action": "save", "folder-1-Inner": r"\\d\same", "folder-1-Outer": r"\\D\SAME"})
    assert resp.status_code == 400 and "already used" in resp.get_data(as_text=True)


def test_forged_fields_for_leds_that_are_not_enabled_are_ignored(ev, cfg):
    post(ev, {"action": "save", "folder-1-Inner": r"\\d\a", "folder-2-Outer": r"\\d\evil", "folder-9-Inner": r"\\d\evil2",
              "folder-1-Bogus": r"\\d\evil3"})
    saved = {(r["table_number"], r["led_type"]): r["shared_folder"] for r in mapping_rows(cfg)}
    assert saved[(1, "Inner")] == r"\\d\a" and len(saved) == 4
    assert not any("evil" in v for v in saved.values())


def test_a_forged_action_is_rejected_before_anything_is_saved(ev, cfg):
    for action in ("delete", "test:2-Outer", "test:../x", "", "TEST-ALL", "test:1-Inner\x00"):
        resp = post(ev, {"action": action, "folder-1-Inner": r"\\d\a"})
        assert resp.status_code == 400, action
    assert all(r["shared_folder"] == "" for r in mapping_rows(cfg))


def test_script_in_a_path_is_never_rendered_as_markup(ev):
    post(ev, {"action": "save", "folder-1-Inner": r"\\d\<script>alert(1)</script>"})
    html = ev.post("/events/1000/mappings", data={"csrf_token": csrf_from(ev, "/events/1000"), "action": "save",
                                                   "folder-1-Inner": r"\\d\<script>alert(1)</script>"}).get_data(as_text=True)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html or "not allowed" in html


def test_a_quote_breakout_in_a_value_is_escaped(ev):
    resp = post(ev, {"action": "save", "ip-1-Inner": '" autofocus onfocus="x'})
    html = resp.get_data(as_text=True)
    assert 'value="" autofocus' not in html and "&#34;" in html


def test_huge_values_are_bounded(ev):
    resp = post(ev, {"action": "save", "folder-1-Inner": "\\\\d\\" + "a" * 20000})
    assert resp.status_code == 400


def test_the_application_data_folder_is_refused(ev, cfg):
    resp = post(ev, {"action": "save", "folder-1-Inner": str(cfg.data_dir / "led")})
    assert resp.status_code == 400 and "reserved" in resp.get_data(as_text=True)


# --- testing (5.3) -------------------------------------------------------------------------------------------

def test_test_all_reports_each_destination_and_records_the_outcome(ev, cfg, shares):
    good, other = folder(shares, "inner1"), folder(shares, "outer1")
    post(ev, {"action": "save", "folder-1-Inner": good, "folder-1-Outer": other,
              "folder-1-MainLED": str(shares / "missing"), "folder-2-Inner": ""})
    resp = post(ev, {"action": "test-all", "folder-1-Inner": good, "folder-1-Outer": other,
                     "folder-1-MainLED": str(shares / "missing"), "folder-2-Inner": ""}, follow=True)
    html = resp.get_data(as_text=True)
    assert "2 of 3 destinations passed." in html
    assert "Table 1 Main LED: Connection Failed" in html and "does not exist" in html
    assert "1 destination has no folder and was not tested." in html
    by = {(r["table_number"], r["led_type"]): r for r in mapping_rows(cfg)}
    assert by[(1, "Inner")]["connection_status"] == "Connection Successful" and by[(1, "Inner")]["last_connection_test"]
    assert by[(1, "MainLED")]["connection_status"] == "Connection Failed"
    assert by[(2, "Inner")]["connection_status"] is None
    ex = db_rows(cfg, "SELECT * FROM exception_log")
    assert len(ex) == 1 and ex[0]["category"] == "Missing folder" and ex[0]["led_type"] == "MainLED"
    assert list(shares.glob("**/.ledsync-probe-*")) == []
    html = page(ev)
    assert html.count("badge-synced") == 2 and html.count("badge-attention") == 1 and "tested-at" in html


def test_a_single_test_saves_the_typed_values_first_and_tests_only_that_row(ev, cfg, shares):
    good = folder(shares, "one")
    resp = post(ev, {"action": "test:1-Inner", "folder-1-Inner": good, "folder-1-Outer": str(shares / "never")}, follow=True)
    html = resp.get_data(as_text=True)
    assert "1 of 1 destination passed: Connection Successful." in html
    by = {(r["table_number"], r["led_type"]): r for r in mapping_rows(cfg)}
    assert by[(1, "Inner")]["shared_folder"] == good and by[(1, "Inner")]["connection_status"] == "Connection Successful"
    assert by[(1, "Outer")]["shared_folder"] == str(shares / "never") and by[(1, "Outer")]["connection_status"] is None


def test_testing_a_row_with_no_folder_says_so(ev):
    resp = post(ev, {"action": "test:1-Outer"}, follow=True)
    assert "has no shared folder to test yet" in resp.get_data(as_text=True)


def test_test_all_with_nothing_mapped_says_so(ev, cfg):
    resp = post(ev, {"action": "test-all"}, follow=True)
    assert "nothing to test yet" in resp.get_data(as_text=True)
    assert db_rows(cfg, "SELECT COUNT(*) AS n FROM operation_log WHERE operation = 'Device Test'")[0]["n"] == 0


def test_an_invalid_form_is_not_tested(ev, cfg, shares):
    good = folder(shares, "g")
    resp = post(ev, {"action": "test-all", "folder-1-Inner": good, "folder-1-Outer": "junk"})
    assert resp.status_code == 400
    assert db_rows(cfg, "SELECT COUNT(*) AS n FROM operation_log WHERE operation = 'Device Test'")[0]["n"] == 0


def test_a_probe_that_cannot_be_removed_shows_a_notice_but_passes(ev, shares):
    good = folder(shares, "g")
    ev.application.extensions["ledsync.checker"] = lambda paths: {
        k: connectivity.CheckResult(True, "ok", None, "A small empty test file could not be removed (x).") for k in paths}
    html = post(ev, {"action": "test-all", "folder-1-Inner": good}, follow=True).get_data(as_text=True)
    assert "could not be removed" in html and "1 of 1 destination passed" in html


def test_a_dead_device_is_reported_within_the_deadline_and_the_page_stays_usable(ev, cfg, monkeypatch):
    real = connectivity.check_many
    ev.application.extensions["ledsync.checker"] = lambda paths: real(paths, timeout=0.3, _check=lambda p: __import__("time").sleep(3))
    html = post(ev, {"action": "test-all", "folder-1-Inner": r"\\dead\share"}, follow=True).get_data(as_text=True)
    assert "did not respond" in html and "Connection Failed" in html
    assert page(ev).count("badge-attention") == 1


def test_failure_messages_show_no_windows_error_text_or_paths_beyond_the_label(ev, shares):
    html = post(ev, {"action": "test:1-Inner", "folder-1-Inner": str(shares / "nope")}, follow=True).get_data(as_text=True)
    flash = re.search(r'alert-error"[^>]*>(.*?)</p>', html, re.S).group(1)
    assert "WinError" not in flash and "Errno" not in flash and "nope" not in flash


# --- history & re-registration -------------------------------------------------------------------------------

def test_the_test_is_audited_with_no_credentials_anywhere(ev, cfg, shares):
    post(ev, {"action": "test:1-Inner", "folder-1-Inner": folder(shares, "a")})
    log = db_rows(cfg, "SELECT operation, status, message FROM operation_log WHERE operation IN ('Device Test','Mapping Saved')")
    assert {r["operation"] for r in log} == {"Device Test", "Mapping Saved"}
    dump = json.dumps([db_rows(cfg, "SELECT * FROM " + t) for t in ("led_mappings", "operation_log", "exception_log")])
    assert not any(word in dump.lower() for word in ("password", "username=", "credential"))


def test_reregistering_with_a_changed_structure_updates_the_page(ev, cfg, shares):
    post(ev, {"action": "save", "folder-1-Inner": folder(shares, "a"), "folder-2-Inner": folder(shares, "b")})
    new = dict(REAL, exportGuid=NEW_GUID, exportTimestamp="2026-09-21T09:30:00.000Z",
               tables=[{"tableNumber": 1, "innerLed": True, "outerLed": False, "mainLed": False},
                       {"tableNumber": 3, "innerLed": False, "outerLed": True, "mainLed": False}])
    serve_event(ev.application.extensions["test.azure"], new)
    resp = ev.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(ev, "/events/new")})
    assert resp.headers["Location"].endswith("/events/reregister")
    token = re.search(r'name="pending_token" value="([^"]+)"', ev.get("/events/reregister").get_data(as_text=True)).group(1)
    resp = ev.post("/events/reregister", data={"pasted_guid": NEW_GUID, "pending_token": token,
                                               "csrf_token": csrf_from(ev, "/events/reregister")})
    assert resp.status_code == 302
    html = page(ev)
    assert 'name="folder-3-Outer"' in html and 'name="folder-2-Inner"' not in html and 'name="folder-1-Outer"' not in html
    assert "Previously mapped" in html and "(1)" in html
    inner = next(r for r in mapping_rows(cfg) if (r["table_number"], r["led_type"]) == (1, "Inner"))
    assert inner["shared_folder"].endswith("a") and inner["connection_status"] is None
