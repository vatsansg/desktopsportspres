"""Phase 6 - the Change Log page: fetch, compare and show (nothing is downloaded or changed)."""

import re

import pytest
from azure.core.exceptions import ServiceRequestError
from conftest import csrf_from, db_rows, serve_event
from phase6_helpers import REAL_GUID_JSON, SAMPLE_LOG, T0, T1, T2, add_history, csv_text

FOLDER = "1000 - Star contender Doha"
NEW_GUID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


@pytest.fixture
def ev(logged_in):
    serve_event(logged_in.application.extensions["test.azure"], dict(REAL_GUID_JSON))
    resp = logged_in.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(logged_in, "/events/new")})
    assert resp.status_code == 302
    return logged_in


def azure_of(client):
    return client.application.extensions["test.azure"]


def put_log(client, text, name="_ledassetschangelog.csv"):
    azure_of(client).put("2026", f"{FOLDER}/{name}", text)


def page(client):
    return client.get("/events/1000/changes").get_data(as_text=True)


def check(client, follow=True):
    return client.post("/events/1000/changes/check", data={"csrf_token": csrf_from(client, "/events/1000/changes")},
                       follow_redirects=follow)


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# --- access ---------------------------------------------------------------------------------------------------------

def test_the_pages_need_login_and_the_launch_cookie(app, client, launched):
    assert client.get("/events/1000/changes").status_code == 403
    assert client.post("/events/1000/changes/check").status_code == 403
    resp = launched.get("/events/1000/changes")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    token = csrf_from(launched, "/login")
    resp = launched.post("/events/1000/changes/check", data={"csrf_token": token})
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_a_check_without_a_csrf_token_is_refused_and_contacts_nothing(ev):
    calls = len(azure_of(ev).calls)
    assert ev.post("/events/1000/changes/check").status_code == 403
    assert len(azure_of(ev).calls) == calls


def test_unknown_or_malformed_events_get_the_branded_404(ev):
    for bad in ("9999", "abc", "..%2f..", "new"):
        assert ev.get(f"/events/{bad}/changes").status_code == 404, bad
    assert ev.post("/events/9999/changes/check", data={"csrf_token": csrf_from(ev, "/events/1000/changes")}).status_code == 404


def test_event_ids_are_matched_case_insensitively(logged_in):
    obj = dict(REAL_GUID_JSON, eventId="Doha-A", exportGuid=NEW_GUID)
    serve_event(azure_of(logged_in), obj)
    logged_in.post("/events/new", data={"event_id": "Doha-A", "csrf_token": csrf_from(logged_in, "/events/new")})
    assert logged_in.get("/events/doha-a/changes").status_code == 200


def test_the_event_details_page_links_to_the_change_log(ev):
    assert 'href="/events/1000/changes"' in ev.get("/events/1000").get_data(as_text=True)


# --- before a check ------------------------------------------------------------------------------------------------------

def test_before_any_check_the_page_explains_and_offers_one_orange_button(ev):
    html = page(ev)
    assert "No check has been run yet" in html and html.count("btn-primary") == 1 and "CHECK FOR CHANGES" in html
    assert "Nothing is downloaded" in html and "data-busy-text" in html


# --- a real check (fake Azure) -------------------------------------------------------------------------------------------

def test_the_real_exported_change_log_is_listed_with_the_four_way_split_intact(ev):
    put_log(ev, SAMPLE_LOG.read_bytes().decode())
    html = check(ev).get_data(as_text=True)
    assert "Checked 83 change log entries" in html and "Nothing was downloaded or changed" in html
    text = text_of(html)
    import csv
    with open(SAMPLE_LOG, encoding="utf-8", newline="") as handle:
        distinct = len({row["filename"].casefold() for row in csv.DictReader(handle)})       # counted independently
    assert f"83 entries covering {distinct} distinct files" in text
    assert "Table 1 Inner sponsorsequence.csv" in text and "Table 1 Outer sponsorsequence.csv" in text
    assert "RPI/HOME_Look.png" in html and "Table / LED-type folder" in text
    assert "file _ledassetschangelog.csv" in text


def test_counts_and_rows_for_a_known_log_with_history(ev, cfg):
    put_log(ev, csv_text([
        ("Table 1/Inner/new.png", T1, "New"), ("Table 1/Inner/upd.png", T2, "Updated"),
        ("Table 1/Inner/done.png", T1, "New"), ("Table 1/Outer/gone.png", T2, "Deleted"),
        ("Table 1/Outer/never-had.png", T2, "Deleted"), ("Table 2/Inner/x.png", T1, "New"),
        ("RPI/logo.png", T0, "New"), ("Table 2/Outer/notused.png", T0, "New")]))
    from ledsync.db import connect
    conn = connect(cfg.db_path)
    add_history(conn, "1000", 1, "Inner", "upd.png", T1)
    add_history(conn, "1000", 1, "Inner", "done.png", T1)
    add_history(conn, "1000", 1, "Outer", "gone.png", T1)
    conn.close()
    html = check(ev).get_data(as_text=True)
    text = text_of(html)
    assert "Waiting To Be Processed (4)" in text                     # new, upd, gone (delete), x
    assert "New 2 Updated 1 Removed In Cloud 1 Already Processed 2 Not Applicable 2 Unreadable Rows 0" in text
    assert "the local copy will be deleted" in text and "Table 2 Outer is not part of this event" in text
    assert "Already Processed (2)" in text and "Not Applicable To This Event (2)" in text


def test_the_result_survives_a_reload_without_contacting_azure_again(ev):
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]))
    check(ev)
    calls = len(azure_of(ev).calls)
    assert "Waiting To Be Processed (1)" in text_of(page(ev)) and page(ev) == page(ev)
    assert len(azure_of(ev).calls) == calls


def test_a_second_check_replaces_the_first(ev):
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]))
    check(ev)
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New"), ("Table 1/Inner/b.png", T1, "New")]))
    assert "Waiting To Be Processed (2)" in text_of(check(ev).get_data(as_text=True))


def test_the_brd_spelling_of_the_file_is_used_when_that_is_all_there_is(ev):
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]), name="_ledassetchangelog.csv")
    assert "file _ledassetchangelog.csv" in text_of(check(ev).get_data(as_text=True))


def test_unreadable_rows_are_shown_and_the_rest_still_used(ev):
    put_log(ev, "sno,filename,changetimestamp,status\n1,Table 1/Inner/a.png,%s,New\n2,../x.png,%s,New\n3,Table 1/Inner/b.png,bad,New\n" % (T0, T0))
    html = check(ev).get_data(as_text=True)
    text = text_of(html)
    assert "Waiting To Be Processed (1)" in text and "Unreadable Rows (2)" in text
    assert "Line 3:" in text and "Line 4:" in text


def test_long_lists_are_capped_on_screen_but_counted_in_full(ev):
    put_log(ev, csv_text([(f"Table 1/Inner/f{i:04d}.png", T0, "New") for i in range(620)]))
    text = text_of(check(ev).get_data(as_text=True))
    assert "Waiting To Be Processed (620)" in text and "Showing the first 500 of 620" in text


# --- failures are plain, logged and never leave a half result -------------------------------------------------------------

def failures(cfg):
    return db_rows(cfg, "SELECT category, operation, message, event_id FROM exception_log ORDER BY exception_id")


def test_no_change_log_yet(ev, cfg):
    html = check(ev).get_data(as_text=True)
    assert "no change log yet" in html
    assert [f["category"] for f in failures(cfg)] == ["Missing folder"] and failures(cfg)[0]["operation"] == "Check Changes"
    assert "No check has been run yet" in page(ev)


def test_a_reexported_event_must_be_registered_again_first(ev, cfg):
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]))
    serve_event(azure_of(ev), dict(REAL_GUID_JSON, exportGuid=NEW_GUID))
    html = check(ev).get_data(as_text=True)
    assert "exported this event again" in html and "Re-register" in html
    assert [f["category"] for f in failures(cfg)] == ["GUID validation"]
    assert "No check has been run yet" in page(ev)


def test_an_unusable_file_is_explained(ev, cfg):
    put_log(ev, "this,is,not,a,change,log\n1,2,3\n")
    assert "expected columns" in check(ev).get_data(as_text=True)
    assert failures(cfg)[0]["category"] == "Configuration"


def test_azure_problems_are_reported_without_azure_text(ev, cfg):
    azure_of(ev).fail_with = ServiceRequestError("dns failure secret-host.example")
    html = check(ev).get_data(as_text=True)
    assert "Could not reach Azure Storage" in html and "secret-host" not in html
    assert failures(cfg)[0]["category"] == "Azure Storage connectivity"


def test_a_wrong_key_is_reported_as_a_permission_problem(ev, cfg, monkeypatch):
    azure_of(ev).expected_key = "SOMETHING" + "Z" * 79
    assert "refused" in check(ev).get_data(as_text=True)
    assert failures(cfg)[0]["category"] == "Permission"


def test_cloud_storage_not_set_up(ev, cfg, monkeypatch):
    for name in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY"):
        monkeypatch.delenv(name)
    calls = len(azure_of(ev).calls)
    assert "Cloud storage is not set up yet" in check(ev).get_data(as_text=True)
    assert len(azure_of(ev).calls) == calls


def test_a_storage_address_in_the_file_that_points_elsewhere_is_not_trusted(ev, cfg):
    bad = dict(REAL_GUID_JSON, eventStorageUrl="https://evil.blob.core.windows.net/2026/x")
    serve_event(azure_of(ev), bad, folder=FOLDER, fix_url=False)
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]))
    assert "not trusted" in check(ev).get_data(as_text=True)


# --- safety --------------------------------------------------------------------------------------------------------------

def test_a_check_only_reads_azure_and_changes_no_file_or_history(ev, cfg):
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New"), ("Table 1/Inner/b.png", T0, "Deleted")]))
    before_files = sorted(p.name for p in cfg.data_dir.iterdir())
    before = {t: db_rows(cfg, f"SELECT * FROM {t}") for t in ("download_history", "sync_history", "led_mappings", "events")}
    check(ev)                                       # the fake raises if any write operation is attempted
    assert {t: db_rows(cfg, f"SELECT * FROM {t}") for t in before} == before
    assert sorted(p.name for p in cfg.data_dir.iterdir()) == before_files
    assert set(azure_of(ev).calls) <= {"list_containers", "list", "download"}


def test_a_check_is_audited_with_counts_only(ev, cfg):
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]))
    check(ev)
    log = db_rows(cfg, "SELECT status, message FROM operation_log WHERE operation = 'Check Changes'")
    assert log == [{"status": "Success", "message": "1 to download, 0 to remove, 0 already processed, 0 not applicable, "
                                                    "0 unreadable row(s) in _ledassetschangelog.csv."}]


def test_hostile_file_names_never_reach_the_page_as_markup(ev):
    put_log(ev, csv_text([("Table 1/Inner/<script>alert(1)</script>.png", T0, "New"),             # refused: < > are not Windows-legal
                          ("Table 1/Inner/'><img src=x onerror=alert(1)>.png", T0, "New"),       # refused
                          ("Table 1/Inner/onerror=alert(1) a&b {{7+7}} {% raw %}.png", T0, "New"),  # legal on Windows: must be escaped
                          ("Table 9/Inner/x&y'z.png", T0, "New")]))                                 # not applicable path, shown escaped
    html = check(ev).get_data(as_text=True)
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "Unreadable Rows (2)" in text_of(html)
    assert "onerror=alert(1) a&amp;b {{7+7}} {% raw %}.png" in html          # shown as plain text, not evaluated
    assert "x&amp;y&#39;z.png" in html


def test_each_event_has_its_own_result(ev, logged_in):
    other = dict(REAL_GUID_JSON, eventId="2000", eventName="Other", exportGuid=NEW_GUID)
    serve_event(azure_of(ev), other)
    ev.post("/events/new", data={"event_id": "2000", "csrf_token": csrf_from(ev, "/events/new")})
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]))
    check(ev)
    assert "No check has been run yet" in ev.get("/events/2000/changes").get_data(as_text=True)
