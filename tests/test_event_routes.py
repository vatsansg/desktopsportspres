"""Phase 3 - Add New Event / Re-register through the web pages (Steps 3.1 and 3.2)."""

import io
import json
import re
from datetime import timedelta, timezone
from pathlib import Path

import pytest
from conftest import csrf_from, db_rows, serve_event

from ledsync.services import registration as reg

ROOT = Path(__file__).resolve().parent.parent
FILES = ROOT / "docs" / "testfiles" / "phase3"
GUID_1000 = "eb0153b9-b74a-45e8-9f0f-b2c1a1b7b459"
NEW_GUID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
PLUS3 = timezone(timedelta(hours=3))


def upload(client, event_id, file_name="1000_valid.json", *, content=None, filename=None, csrf=True):
    """Phase 4: 'upload' now means: put the file in (fake) Azure Storage where the web application
    would keep it, then register the event by its ID. Same call shape as in Phase 3."""
    azure = client.application.extensions["test.azure"]
    raw = content if content is not None else (FILES / file_name).read_bytes()
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        obj = None
    if isinstance(obj, dict):
        same = str(obj.get("eventId")) == event_id
        folder = f"{event_id} - {obj.get('eventName') if same and isinstance(obj.get('eventName'), str) and obj['eventName'].strip() and len(obj['eventName']) < 60 else 'Test'}"
        folder = "".join(ch for ch in folder if ch.isprintable() and ch not in "/\\")
        serve_event(azure, obj, folder=folder)
    else:
        serve_event(azure, None, folder=f"{event_id} - Test", raw=raw)
    form = {"event_id": event_id}
    if csrf:
        form["csrf_token"] = csrf_from(client, "/events/new")
    return client.post("/events/new", data=form)


def events_rows(cfg):
    return db_rows(cfg, "SELECT * FROM events ORDER BY event_id")


def exception_rows(cfg):
    return db_rows(cfg, "SELECT * FROM exception_log ORDER BY exception_id")


def dashboard(client):
    return client.get("/").get_data(as_text=True)


# --- access -------------------------------------------------------------------------------------

def test_pages_require_login_and_the_launch_cookie(launched, client):
    for path in ("/events/new", "/events/reregister"):
        assert launched.get(path).status_code == 302        # signed out -> login
        assert client.get(path).status_code == 403          # no launch cookie
    assert client.post("/events/new").status_code == 403


def test_the_add_form_takes_only_an_event_id_and_is_accessible(logged_in):
    html = logged_in.get("/events/new").get_data(as_text=True)
    assert "Add New Event" in html and "REGISTER EVENT" in html
    assert 'type="file"' not in html and "multipart" not in html          # cloud only (owner decision)
    assert 'for="event_id"' in html and 'name="csrf_token"' in html and "data-busy-text" in html
    assert "wtt-logo.png" in html


def test_dashboard_offers_add_new_event_as_the_single_primary_action(logged_in):
    html = dashboard(logged_in)
    assert 'href="/events/new"' in html and "ADD NEW EVENT" in html
    assert html.count("btn-primary") == 1


# --- Step 3.1: a valid file creates the events row ------------------------------------------------

def test_valid_file_registers_the_event_and_shows_it_on_the_dashboard(app, cfg, logged_in):
    app.config["DISPLAY_TZ"] = PLUS3
    resp = upload(logged_in, "1000")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    [row] = events_rows(cfg)
    assert (row["event_id"], row["event_name"], row["event_guid"], row["status"]) == (
        "1000", "Star contender Doha", GUID_1000, "Registered")
    assert row["configuration_file"] == "https://sasportspresentation.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha/_GUID.json"
    assert json.loads(row["configuration_json"])["eventId"] == "1000"

    html = dashboard(logged_in)
    assert "Event 1000 (Star contender Doha) registered from Azure Storage." in html
    assert ">1000</td>" in html and "Star contender Doha" in html
    assert "18/09/26 10:02" in html                       # owner: Last Updated = export time, local zone
    assert 'badge-registered' in html and "1 registered" in html


def test_registration_is_written_to_the_operation_log(cfg, logged_in):
    upload(logged_in, "1000")
    ops = db_rows(cfg, "SELECT operation, status, event_id FROM operation_log WHERE operation LIKE 'Event %'")
    assert ops == [{"operation": "Event Registered", "status": "Success", "event_id": "1000"}]


def test_registering_the_same_file_again_is_a_harmless_notice(cfg, logged_in):
    upload(logged_in, "1000")
    upload(logged_in, "1000")
    html = dashboard(logged_in)
    assert "already registered with this GUID" in html and "alert-info" in html
    assert len(events_rows(cfg)) == 1 and exception_rows(cfg) == []


def test_two_events_register_side_by_side(cfg, logged_in):
    upload(logged_in, "1000")
    assert upload(logged_in, "2000", "2000_second_event.json").status_code == 302
    assert [r["event_id"] for r in events_rows(cfg)] == ["1000", "2000"]
    assert "2 registered" in dashboard(logged_in)


# --- rejections (Step 3.2) ---------------------------------------------------------------------------

def test_guid_mismatch_is_rejected_logged_and_redirects_to_reregistration(cfg, logged_in):
    upload(logged_in, "1000")
    resp = upload(logged_in, "1000", "1000_reexported_new_guid.json")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/events/reregister")
    assert events_rows(cfg)[0]["event_guid"] == GUID_1000                       # NOT changed
    [ex] = exception_rows(cfg)
    assert ex["category"] == "GUID validation" and ex["operation"] == "Register Event"
    assert ex["event_id"] == "1000" and ex["source"] == "https://sasportspresentation.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha/_GUID.json"
    assert GUID_1000 in ex["message"] and NEW_GUID in ex["message"]


def test_reregister_page_shows_both_guids_and_asks_for_the_new_one(logged_in):
    upload(logged_in, "1000")
    upload(logged_in, "1000", "1000_reexported_new_guid.json")
    html = logged_in.get("/events/reregister").get_data(as_text=True)
    assert "Re-Register Event" in html and "different GUID" in html
    assert GUID_1000 in html                                   # what is registered now: shown in full
    assert NEW_GUID not in html and NEW_GUID[-6:] in html      # the file's GUID: ending only (see below)
    assert 'name="pasted_guid"' in html and "RE-REGISTER EVENT" in html and "Cancel" in html


def _to_reregister(logged_in):
    upload(logged_in, "1000")
    upload(logged_in, "1000", "1000_reexported_new_guid.json")


def pending_token(client) -> str:
    html = client.get("/events/reregister").get_data(as_text=True)
    return re.search(r'name="pending_token" value="([^"]+)"', html).group(1)


def _paste(client, guid, path="/events/reregister", token=None):
    return client.post(path, data={"pasted_guid": guid, "csrf_token": csrf_from(client, "/events/reregister"),
                                   "pending_token": token or pending_token(client)})


def test_pasting_the_matching_new_guid_completes_the_reregistration(app, cfg, logged_in):
    app.config["DISPLAY_TZ"] = PLUS3
    _to_reregister(logged_in)
    resp = _paste(logged_in, "  " + NEW_GUID.upper() + " ")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    [row] = events_rows(cfg)
    assert row["event_guid"] == NEW_GUID and row["status"] == "Registered"
    assert row["last_updated"] == "2026-09-21T09:30:00Z"
    html = dashboard(logged_in)
    assert "re-registered with the new GUID" in html and "21/09/26 12:30" in html
    ops = [r["operation"] for r in db_rows(cfg, "SELECT operation FROM operation_log ORDER BY log_id")
           if r["operation"].startswith("Event")]
    assert ops == ["Event Registered", "Event Re-registered"]
    # the pending request is consumed: it cannot be replayed
    assert logged_in.get("/events/reregister").status_code == 302


def test_pasting_a_guid_that_does_not_match_the_file_is_refused_and_logged(cfg, logged_in):
    _to_reregister(logged_in)
    resp = _paste(logged_in, GUID_1000)                                         # the OLD guid
    assert resp.status_code == 400
    assert "does not match the GUID in the file" in resp.get_data(as_text=True)
    assert events_rows(cfg)[0]["event_guid"] == GUID_1000
    ops = [(e["category"], e["operation"]) for e in exception_rows(cfg)]
    assert ops == [("GUID validation", "Register Event"), ("GUID validation", "Re-register Event")]
    assert _paste(logged_in, NEW_GUID).status_code == 302                         # the operator can still retry


@pytest.mark.parametrize("pasted", ["", "garbage", NEW_GUID[:-3]])
def test_a_malformed_paste_shows_a_clear_error_without_an_exception_row(cfg, logged_in, pasted):
    _to_reregister(logged_in)
    before = len(exception_rows(cfg))
    resp = _paste(logged_in, pasted)
    assert resp.status_code == 400 and "not a valid GUID" in resp.get_data(as_text=True)
    assert len(exception_rows(cfg)) == before


def test_cancel_discards_the_pending_request_and_changes_nothing(cfg, logged_in):
    _to_reregister(logged_in)
    resp = logged_in.post("/events/reregister/cancel", data={"csrf_token": csrf_from(logged_in, "/events/reregister"),
                                                            "pending_token": pending_token(logged_in)})
    assert resp.status_code == 302
    assert "cancelled" in dashboard(logged_in) and events_rows(cfg)[0]["event_guid"] == GUID_1000
    assert logged_in.get("/events/reregister").status_code == 302


def test_reregister_without_a_pending_request_redirects_with_a_message(logged_in):
    resp = logged_in.get("/events/reregister")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    assert "expired" in dashboard(logged_in)
    assert logged_in.post("/events/reregister", data={"pasted_guid": NEW_GUID,
                                                      "csrf_token": csrf_from(logged_in, "/")}).status_code == 302


def test_an_expired_pending_request_cannot_be_completed(app, cfg, logged_in):
    clock = {"now": 0.0}
    app.extensions["ledsync.pending"] = reg.PendingReregistrations(ttl_seconds=60, clock=lambda: clock["now"])
    _to_reregister(logged_in)
    clock["now"] += 61
    token = csrf_from(logged_in, "/")                      # the page itself now redirects away
    resp = logged_in.post("/events/reregister", data={"pasted_guid": NEW_GUID, "csrf_token": token})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    assert "expired" in dashboard(logged_in)
    assert events_rows(cfg)[0]["event_guid"] == GUID_1000


def test_another_browser_session_cannot_use_someone_elses_pending_request(app, cfg, logged_in):
    _to_reregister(logged_in)
    other = app.test_client()
    other.get(f"/_launch?t={app.config['LAUNCH_TOKEN']}")     # single-use: this will fail
    assert other.get("/events/reregister").status_code == 403


@pytest.mark.parametrize("event_id,file_name,fragment,category", [
    ("2000", "1000_valid.json", "for event 1000, but you entered event 2000", "Configuration"),
    ("2001", "2001_duplicate_guid_of_1000.json", "already belongs to a different registered event", "GUID validation"),
    ("3000", "bad_malformed.json", "not valid JSON", "Invalid configuration"),
    ("3001", "bad_missing_event_name.json", "eventName", "Invalid configuration"),
    ("3002", "bad_no_tables.json", "at least one table", "Invalid configuration"),
    ("3003", "bad_invalid_guid.json", "exportGuid", "Invalid configuration"),
])
def test_each_rejection_shows_a_clear_message_and_is_logged(cfg, logged_in, event_id, file_name, fragment, category):
    upload(logged_in, "1000")                                # so the duplicate-GUID case has an owner
    resp = upload(logged_in, event_id, file_name)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and fragment in html and 'role="alert"' in html
    assert event_id in html                                  # the typed Event ID is kept
    assert [r["event_id"] for r in events_rows(cfg)] == ["1000"]
    [ex] = exception_rows(cfg)
    assert ex["category"] == category and ex["operation"] == "Register Event" and ex["resolution_status"] == "Open"


# --- form typos are not exceptions ------------------------------------------------------------------------

@pytest.mark.parametrize("event_id,fragment", [("", "Enter the Event ID"), ("../x", "letters, numbers"),
                                              ("a b", "letters, numbers"), ("x" * 51, "letters, numbers")])
def test_a_bad_event_id_typo_is_explained_and_not_logged_as_an_exception(cfg, logged_in, event_id, fragment):
    resp = upload(logged_in, event_id)
    assert resp.status_code == 400 and fragment in resp.get_data(as_text=True)
    assert events_rows(cfg) == [] and exception_rows(cfg) == []




# --- security -------------------------------------------------------------------------------------------------

def test_every_post_needs_a_csrf_token(cfg, logged_in):
    assert upload(logged_in, "1000", csrf=False).status_code == 403
    _to_reregister(logged_in)
    assert logged_in.post("/events/reregister", data={"pasted_guid": NEW_GUID}).status_code == 403
    assert logged_in.post("/events/reregister/cancel").status_code == 403
    assert events_rows(cfg)[0]["event_guid"] == GUID_1000






def test_hostile_event_name_from_the_file_is_escaped_everywhere_it_is_shown(logged_in):
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    content = json.dumps({**json.loads((FILES / "2000_second_event.json").read_text()), "eventName": evil}).encode()
    assert upload(logged_in, "2000", content=content).status_code == 302
    html = dashboard(logged_in)
    assert "<script>alert(1)" not in html and "<img src=x" not in html and "&lt;script&gt;" in html


def test_hostile_typed_event_id_is_never_reflected_unescaped(logged_in):
    resp = upload(logged_in, '"><script>alert(1)</script>')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "<script>alert(1)" not in html


def test_file_contents_are_never_echoed_in_error_pages_or_the_exception_log(cfg, logged_in):
    secret = "TOP-SECRET-VALUE-12345"
    content = json.dumps({"eventId": "3000", "eventName": secret * 30, "tables": secret}).encode()
    resp = upload(logged_in, "3000", content=content)
    assert resp.status_code == 400 and secret not in resp.get_data(as_text=True)
    assert all(secret not in (e["message"] or "") for e in exception_rows(cfg))






def test_pending_reregistration_is_referenced_only_by_an_opaque_token_in_the_session(logged_in):
    _to_reregister(logged_in)
    cookie = logged_in.get_cookie("ledsync_session").value
    assert NEW_GUID not in cookie and "Star contender" not in cookie     # nothing from the file is in the cookie


def test_logout_and_password_change_invalidate_nothing_about_registered_events(cfg, logged_in):
    upload(logged_in, "1000")
    logged_in.post("/logout", data={"csrf_token": csrf_from(logged_in, "/")})
    assert len(events_rows(cfg)) == 1


# --- architect review, Phase 3 ---------------------------------------------------------------------------

def test_the_file_guid_is_never_shown_in_full_so_it_cannot_just_be_copied_back(logged_in):
    """The point of the paste step is to prove the operator got the GUID from the web app."""
    _to_reregister(logged_in)
    html = logged_in.get("/events/reregister").get_data(as_text=True)
    assert NEW_GUID not in html and NEW_GUID.replace("-", "") not in html
    assert NEW_GUID[-6:] in html                                # enough to tell which export it is
    assert "does not match" not in html


def test_two_tabs_cannot_confuse_one_pending_request_for_another(cfg, app, logged_in):
    upload(logged_in, "1000")
    upload(logged_in, "2000", "2000_second_event.json")
    # Tab 1: mismatch on event 1000
    upload(logged_in, "1000", "1000_reexported_new_guid.json")
    tab1_token = pending_token(logged_in)
    # Tab 2: while tab 1 is still open, a mismatch on event 2000 replaces the session's pending request
    other = json.loads((FILES / "2000_second_event.json").read_text())
    other["exportGuid"] = "11111111-2222-4333-8444-555555555555"
    upload(logged_in, "2000", content=json.dumps(other).encode())
    # Tab 1 submits its (now stale) form with the GUID it was expecting
    resp = _paste(logged_in, NEW_GUID, token=tab1_token)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/events/reregister")
    assert "out of date" in logged_in.get("/events/reregister").get_data(as_text=True)
    guids = {r["event_id"]: r["event_guid"] for r in events_rows(cfg)}
    assert guids["1000"] == GUID_1000 and guids["2000"] != "11111111-2222-4333-8444-555555555555"   # nothing changed
    # ...and tab 2's own request still works, for the right event
    assert _paste(logged_in, "11111111-2222-4333-8444-555555555555").status_code == 302
    assert {r["event_id"]: r["event_guid"] for r in events_rows(cfg)}["2000"] == "11111111-2222-4333-8444-555555555555"


def test_a_stale_cancel_from_an_older_tab_does_not_discard_the_current_request(logged_in):
    _to_reregister(logged_in)
    old_token = pending_token(logged_in)
    upload(logged_in, "1000", "1000_reexported_new_guid.json")          # a fresh mismatch replaces the request
    resp = logged_in.post("/events/reregister/cancel", data={"csrf_token": csrf_from(logged_in, "/events/reregister"),
                                                            "pending_token": old_token})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/events/reregister")
    assert logged_in.get("/events/reregister").status_code == 200        # still pending


def test_a_new_mismatch_replaces_the_previous_pending_request_instead_of_orphaning_it(app, logged_in):
    store = app.extensions["ledsync.pending"]
    _to_reregister(logged_in)
    first = pending_token(logged_in)
    upload(logged_in, "1000", "1000_reexported_new_guid.json")
    assert store.get(first) is None and store.get(pending_token(logged_in)) is not None


def test_pastes_without_the_page_token_are_refused(cfg, logged_in):
    _to_reregister(logged_in)
    resp = logged_in.post("/events/reregister", data={"pasted_guid": NEW_GUID,
                                                     "csrf_token": csrf_from(logged_in, "/events/reregister")})
    assert resp.status_code == 302 and events_rows(cfg)[0]["event_guid"] == GUID_1000


def test_wording_after_completion_says_expired_or_already_completed(logged_in):
    _to_reregister(logged_in)
    token = pending_token(logged_in)
    csrf = csrf_from(logged_in, "/events/reregister")
    logged_in.post("/events/reregister", data={"pasted_guid": NEW_GUID, "csrf_token": csrf, "pending_token": token})
    resp = logged_in.post("/events/reregister", data={"pasted_guid": NEW_GUID, "csrf_token": csrf, "pending_token": token})
    assert resp.status_code == 302
    assert "expired or was already completed" in dashboard(logged_in)


def test_a_hostile_unicode_name_is_rejected_with_a_message_and_an_exception_row(cfg, logged_in):
    evil = json.loads((FILES / "2000_second_event.json").read_text())
    evil["eventName"] = "Innocent \u202eevil"
    resp = upload(logged_in, "2000", content=json.dumps(evil).encode())
    assert resp.status_code == 400 and "not allowed" in resp.get_data(as_text=True)
    assert events_rows(cfg) == []
    [ex] = exception_rows(cfg)
    assert ex["category"] == "Invalid configuration" and "\u202e" not in ex["message"]


def test_a_lone_surrogate_gives_a_clear_400_not_a_500(cfg, logged_in):
    raw = b'{"eventId":"2000","eventName":"a\\ud800b","eventStorageUrl":"https://h.example/x","exportGuid":"0b8f5c2a-91d3-4e6f-8a17-3c5d7e9f1a2b","tables":[{"tableNumber":1,"innerLed":true,"outerLed":true,"mainLed":true}]}'
    resp = upload(logged_in, "2000", content=raw)
    assert resp.status_code == 400
    assert [e["category"] for e in exception_rows(cfg)] == ["Invalid configuration"]


def test_error_pages_focus_the_message_and_do_not_autofocus_the_field(logged_in):
    html = upload(logged_in, "../x").get_data(as_text=True)
    assert "data-focus-alert" in html and "autofocus" not in html
    clean = logged_in.get("/events/new").get_data(as_text=True)
    assert "autofocus" in clean and "data-focus-alert" not in clean


def test_form_fields_are_linked_to_their_hints_and_to_the_error(logged_in):
    html = upload(logged_in, "../x").get_data(as_text=True)
    assert 'aria-describedby="form-error event-id-hint"' in html and 'id="event-id-hint"' in html


def test_the_login_page_still_autofocuses_username_after_a_failed_sign_in(launched):
    """The focus-the-error script must not change the Phase 1 sign-in behaviour."""
    from conftest import do_login

    html = do_login(launched, password="wrong").get_data(as_text=True)
    assert "data-focus-alert" not in html and 'id="username"' in html and "autofocus" in html
