"""Phase 4 - Step 4.2: registering an event by downloading its _GUID.json from Azure Storage,
and Step 4.1: the Cloud Storage settings screen. Uses a faithful fake of the Azure SDK."""

import json
import re
from pathlib import Path

import pytest
from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError
from conftest import csrf_from, db_rows, event_folder, event_url, serve_event
from fakes import ACCOUNT, FAKE_KEY, http_error

from ledsync.db import connect
from ledsync.services import cloud, exceptions
from ledsync.services import registration as reg
from ledsync.services import settings as cs
from ledsync.services.storage import EventLocation

ROOT = Path(__file__).resolve().parent.parent
FILES = ROOT / "docs" / "testfiles" / "phase3"
GUID_1000 = "eb0153b9-b74a-45e8-9f0f-b2c1a1b7b459"
NEW_GUID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SRC_1000 = "https://sasportspresentation.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha/_GUID.json"


def register(client, event_id):
    return client.post("/events/new", data={"event_id": event_id, "csrf_token": csrf_from(client, "/events/new")})


def events(cfg):
    return db_rows(cfg, "SELECT * FROM events ORDER BY event_id")


def exc_rows(cfg):
    return db_rows(cfg, "SELECT * FROM exception_log ORDER BY exception_id")


def dash(client):
    return client.get("/").get_data(as_text=True)


# =========================== Step 4.2: register from Azure ===========================

def test_the_real_test_event_registers_from_azure_and_every_field_is_stored(app, azure, cfg, logged_in):
    """Step 4.2 validation (against the fake): eventId, eventName, eventStorageUrl, tables, exportGuid and
    export metadata are parsed and stored."""
    azure.put("2026", "1000 - Star contender Doha/_GUID.json", (FILES / "1000_valid.json").read_bytes())
    resp = register(logged_in, "1000")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")
    [row] = events(cfg)
    assert (row["event_id"], row["event_name"], row["event_guid"], row["status"]) == (
        "1000", "Star contender Doha", GUID_1000, "Registered")
    assert row["configuration_file"] == SRC_1000                 # BRD 7.1: the source _GUID.json location
    stored = json.loads(row["configuration_json"])
    assert stored["eventStorageUrl"] == "https://sasportspresentation.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha"
    assert [t["tableNumber"] for t in stored["tables"]] == [1, 2]
    assert stored["exportedByRole"] == "Administrator" and stored["exportGuid"] == GUID_1000
    assert row["last_updated"] == "2026-09-18T07:02:26.380Z"
    assert "registered from Azure Storage" in dash(logged_in)


def test_registering_only_ever_reads_from_azure(app, azure, cfg, logged_in):
    azure.put("2026", "1000 - Star contender Doha/_GUID.json", (FILES / "1000_valid.json").read_bytes())
    register(logged_in, "1000")
    # (any write call on the fake raises AssertionError and would fail this test)
    assert set(azure.calls) <= {"list_containers", "list", "download"}
    assert azure.given_key == FAKE_KEY and azure.account_url == f"https://{ACCOUNT}.blob.core.windows.net"


def test_event_is_found_in_another_year_container(app, azure, cfg, logged_in):
    obj = json.loads((FILES / "2000_second_event.json").read_text())
    serve_event(azure, obj, container="2025")
    register(logged_in, "2000")
    [row] = events(cfg)
    assert row["configuration_file"].startswith("https://sasportspresentation.blob.core.windows.net/2025/")


def test_the_add_form_is_busy_aware_and_the_default_container_comes_from_settings(logged_in):
    html = logged_in.get("/events/new").get_data(as_text=True)
    assert 'data-busy-text="CONTACTING AZURE' in html


def test_re_export_flow_works_through_azure_end_to_end(app, azure, cfg, logged_in):
    azure.put("2026", "1000 - Star contender Doha/_GUID.json", (FILES / "1000_valid.json").read_bytes())
    register(logged_in, "1000")
    obj = json.loads((FILES / "1000_reexported_new_guid.json").read_text())
    serve_event(azure, obj, folder="1000 - Star contender Doha")           # the web app exported it again
    resp = register(logged_in, "1000")
    assert resp.headers["Location"].endswith("/events/reregister")
    assert [e["category"] for e in exc_rows(cfg)] == ["GUID validation"]
    html = logged_in.get("/events/reregister").get_data(as_text=True)
    token = re.search(r'name="pending_token" value="([^"]+)"', html).group(1)
    logged_in.post("/events/reregister", data={"pasted_guid": NEW_GUID, "pending_token": token,
                                              "csrf_token": csrf_from(logged_in, "/events/reregister")})
    assert events(cfg)[0]["event_guid"] == NEW_GUID


# --- not configured ---------------------------------------------------------------------------------------

def test_registering_without_cloud_settings_explains_and_links_to_settings(app, azure, cfg, logged_in, monkeypatch):
    for name in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY", "STORAGE_CONTAINER"):
        monkeypatch.delenv(name)
    resp = register(logged_in, "1000")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "Cloud storage is not set up yet" in html
    assert 'href="/settings/cloud"' in html
    assert azure.calls == [] and exc_rows(cfg) == []                       # nothing contacted, nothing logged


# --- Azure problems: category, plain message, exception log, no leak ---------------------------------------------

@pytest.mark.parametrize("failure,category,fragment", [
    (ServiceRequestError("no route to host secret-request-id-abc123"), "Azure Storage connectivity", "Could not reach Azure"),
    (ClientAuthenticationError("bad key secret-request-id-abc123"), "Permission", "refused the storage account key"),
    (http_error(500), "Azure Storage connectivity", "busy or unavailable"),
])
def test_azure_failures_are_explained_categorised_logged_and_never_leak(app, azure, cfg, logged_in, failure, category, fragment):
    azure.put("2026", "1000 - Star contender Doha/_GUID.json", (FILES / "1000_valid.json").read_bytes())
    azure.fail_with = failure
    resp = register(logged_in, "1000")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and fragment in html and 'role="alert"' in html
    assert "secret-request-id" not in html and FAKE_KEY not in html
    [ex] = exc_rows(cfg)
    assert ex["category"] == category and ex["operation"] == "Register Event" and ex["event_id"] == "1000"
    assert ex["source"] == f"Azure Storage: {ACCOUNT}" and "secret-request-id" not in ex["message"]
    assert events(cfg) == []
    assert register(logged_in, "1000").status_code == 302                       # and it recovers on the next try


def test_a_wrong_key_in_settings_is_a_clear_permission_error(app, azure, cfg, logged_in):
    azure.expected_key = "SOMETHING" + "Z" * 79
    resp = register(logged_in, "1000")
    assert resp.status_code == 400 and "refused the storage account key" in resp.get_data(as_text=True)
    assert exc_rows(cfg)[0]["category"] == "Permission"


def test_an_unknown_event_is_a_missing_folder_error(app, azure, cfg, logged_in):
    azure.containers.add("2026")
    resp = register(logged_in, "4242")
    assert resp.status_code == 400 and "No folder for event 4242" in resp.get_data(as_text=True)
    assert exc_rows(cfg)[0]["category"] == "Missing folder"


def test_a_folder_without_a_guid_file_says_to_run_export_event(app, azure, cfg, logged_in):
    azure.put("2026", "1000 - Star contender Doha/Table 1/Inner/a.png", b"x")
    resp = register(logged_in, "1000")
    assert "Export Event" in resp.get_data(as_text=True) and exc_rows(cfg)[0]["category"] == "Missing folder"


def test_two_folders_for_one_id_is_refused_not_guessed(app, azure, cfg, logged_in):
    azure.put("2026", "1000 - A/_GUID.json", b"{}")
    azure.put("2026", "1000 - B/_GUID.json", b"{}")
    resp = register(logged_in, "1000")
    assert resp.status_code == 400 and "More than one folder" in resp.get_data(as_text=True)
    assert exc_rows(cfg)[0]["category"] == "Configuration" and events(cfg) == []


def test_an_oversized_blob_is_rejected_as_too_large(app, azure, cfg, logged_in):
    azure.put("2026", "1000 - Big/_GUID.json", b" " * 5_000_000)
    resp = register(logged_in, "1000")
    assert resp.status_code == 400 and "too large" in resp.get_data(as_text=True)
    assert azure.downloads[-1][3] == reg.MAX_CONFIG_BYTES + 1                   # never downloaded in full
    assert exc_rows(cfg)[0]["category"] == "Invalid configuration"


def test_bad_configuration_from_azure_is_rejected_exactly_as_in_phase_3(app, azure, cfg, logged_in):
    azure.put("2026", "3000 - Broken/_GUID.json", (FILES / "bad_malformed.json").read_bytes())
    resp = register(logged_in, "3000")
    assert resp.status_code == 400 and "not valid JSON" in resp.get_data(as_text=True)
    [ex] = exc_rows(cfg)
    assert ex["category"] == "Invalid configuration" and ex["source"] == "Azure Storage: sasportspresentation"


# --- S-14: an untrusted file must not steer anything to another location ------------------------------------------

def _serve_with_url(azure, url, event_id="1000", folder="1000 - Star contender Doha", container="2026"):
    obj = json.loads((FILES / "1000_valid.json").read_text())
    obj["eventStorageUrl"] = url
    serve_event(azure, obj, container=container, folder=folder, fix_url=False)


@pytest.mark.parametrize("url,fragment", [
    ("https://evil.example.com/2026/1000%20-%20Star%20contender%20Doha", "different storage account"),
    ("https://otheraccount.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha", "different storage account"),
    ("https://sasportspresentation.blob.core.windows.net.evil.com/2026/1000%20-%20Star%20contender%20Doha", "different storage account"),
    ("https://sasportspresentation.blob.core.windows.net:8443/2026/1000%20-%20Star%20contender%20Doha", "different storage account"),
    ("https://sasportspresentation.blob.core.windows.net/2025/1000%20-%20Star%20contender%20Doha", "does not match the folder"),
    ("https://sasportspresentation.blob.core.windows.net/2026/1000%20-%20Another%20Event", "does not match the folder"),
    ("https://sasportspresentation.blob.core.windows.net/2026", "does not match the folder"),
    ("https://sasportspresentation.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha/../x", "does not match the folder"),
    ("https://sasportspresentation.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha/Table%201", "does not match the folder"),
])
def test_a_storage_address_pointing_anywhere_else_is_refused_and_logged(app, azure, cfg, logged_in, url, fragment):
    _serve_with_url(azure, url)
    resp = register(logged_in, "1000")
    assert resp.status_code == 400 and fragment in resp.get_data(as_text=True)
    assert events(cfg) == []
    [ex] = exc_rows(cfg)
    assert ex["category"] == "Configuration" and ex["source"].startswith("Azure Storage")


@pytest.mark.parametrize("url", [
    "https://sasportspresentation.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha",
    "https://sasportspresentation.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha/",
    "https://SASPORTSPRESENTATION.blob.core.windows.net/2026/1000 - Star contender Doha",
    "https://sasportspresentation.blob.core.windows.net:443/2026/1000%20-%20Star%20contender%20Doha",
])
def test_equivalent_spellings_of_the_right_address_are_accepted(app, azure, cfg, logged_in, url):
    _serve_with_url(azure, url)
    assert register(logged_in, "1000").status_code == 302 and len(events(cfg)) == 1


def test_verify_storage_location_unit():
    cfg_obj = reg.parse_event_config((FILES / "1000_valid.json").read_bytes())
    loc = EventLocation("2026", "1000 - Star contender Doha")
    cloud.verify_storage_location(cfg_obj, ACCOUNT, loc)                        # fine
    with pytest.raises(reg.RegistrationError):
        cloud.verify_storage_location(cfg_obj, "someoneelse", loc)


# --- Event IDs are case-insensitive (owner decision) ------------------------------------------------------------------------

def test_event_ids_differing_only_by_case_are_the_same_event(app, azure, cfg, logged_in):
    obj = json.loads((FILES / "2000_second_event.json").read_text())
    obj["eventId"] = "EVT9"
    serve_event(azure, obj, folder="EVT9 - Second Test Event")
    assert register(logged_in, "EVT9").status_code == 302
    # typed in another case: Azure finds the SAME folder, the GUID matches -> a harmless notice, not a second event
    assert register(logged_in, "evt9").status_code == 302
    assert "already registered" in dash(logged_in)
    assert [e["event_id"] for e in events(cfg)] == ["EVT9"]


def test_a_different_case_spelling_with_a_new_guid_leads_to_re_registration_of_the_same_row(app, azure, cfg, logged_in):
    obj = json.loads((FILES / "2000_second_event.json").read_text())
    obj["eventId"] = "EVT9"
    serve_event(azure, obj, folder="EVT9 - Second Test Event")
    register(logged_in, "EVT9")
    newer = dict(obj, exportGuid="11111111-2222-4333-8444-555555555555")
    serve_event(azure, newer, folder="EVT9 - Second Test Event")                     # the web app exported it again
    resp = register(logged_in, "evt9")                                               # ...and the operator types lower case
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/events/reregister")
    assert "EVT9" in logged_in.get("/events/reregister").get_data(as_text=True)      # the spelling on record
    assert [e["event_id"] for e in events(cfg)] == ["EVT9"]


def test_the_database_itself_refuses_two_ids_that_differ_only_by_case(app, cfg):
    import sqlite3

    conn = connect(cfg.db_path)
    conn.execute("INSERT INTO events (event_id, event_name) VALUES ('abc', 'one')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO events (event_id, event_name) VALUES ('ABC', 'two')")
    conn.close()


def test_leading_zeros_are_significant_01000_is_not_1000(app, azure, cfg, logged_in):
    azure.put("2026", "1000 - Star contender Doha/_GUID.json", (FILES / "1000_valid.json").read_bytes())
    register(logged_in, "1000")
    azure.containers.add("2026")
    resp = register(logged_in, "01000")
    assert resp.status_code == 400 and "No folder for event 01000" in resp.get_data(as_text=True)
    assert [e["event_id"] for e in events(cfg)] == ["1000"]


def test_startup_survives_an_existing_database_that_already_has_case_duplicates(app, cfg):
    from ledsync.db import init_db

    conn = connect(cfg.db_path)
    conn.execute("DROP INDEX IF EXISTS ux_events_event_id_nocase")
    conn.execute("INSERT INTO events (event_id, event_name) VALUES ('abc', 'one')")
    conn.execute("INSERT INTO events (event_id, event_name) VALUES ('ABC', 'two')")
    conn.commit()
    conn.close()
    init_db(cfg.db_path)                                                              # must not raise


# =========================== Step 4.1: Cloud Storage settings ===========================

def settings_page(client):
    return client.get("/settings/cloud").get_data(as_text=True)


def post_settings(client, account=ACCOUNT, container="2026", key="", action="save"):
    return client.post("/settings/cloud", data={"account": account, "container": container, "access_key": key,
                                               "action": action, "csrf_token": csrf_from(client, "/settings/cloud")})


def test_settings_require_login_and_the_launch_cookie(launched, client):
    assert launched.get("/settings/cloud").status_code == 302
    assert client.get("/settings/cloud").status_code == 403
    assert client.post("/settings/cloud").status_code == 403
    assert launched.get("/settings").status_code == 302


def test_settings_link_is_in_the_header_and_the_page_is_branded(logged_in):
    assert 'href="/settings/cloud"' in dash(logged_in)
    html = settings_page(logged_in)
    assert "Cloud Storage" in html and "SAVE SETTINGS" in html and "Test Connection" in html
    assert 'for="account"' in html and 'for="container"' in html and 'for="access_key"' in html
    assert 'type="password"' in html and 'name="access_key"' in html and 'autocomplete="off"' in html
    assert html.count("btn-primary") == 1 and "wtt-logo.png" in html


def test_event_and_asset_path_are_explained_not_configurable(logged_in):
    html = settings_page(logged_in)
    assert "eventStorageUrl" in html and "Not needed" in html
    assert 'name="event_path"' not in html and 'name="asset_path"' not in html


def test_saving_settings_persists_and_shows_a_confirmation(app, cfg, logged_in, monkeypatch):
    for n in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY", "STORAGE_CONTAINER"):
        monkeypatch.delenv(n)
    resp = post_settings(logged_in, "  SasPortsPresentation ", " 2026 ", FAKE_KEY)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/settings/cloud")
    page = settings_page(logged_in)
    assert "Cloud storage settings saved." in page and f'value="{ACCOUNT}"' in page and 'value="2026"' in page
    assert "Saved" in page
    conn = connect(cfg.db_path)                                     # 'after a restart': a brand-new connection
    try:
        s = cs.load_cloud(conn)
    finally:
        conn.close()
    assert (s.account, s.container, s.access_key) == (ACCOUNT, "2026", FAKE_KEY)


def test_the_key_is_never_shown_again_anywhere_after_it_is_saved(app, cfg, logged_in, monkeypatch):
    for n in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY", "STORAGE_CONTAINER"):
        monkeypatch.delenv(n)
    post_settings(logged_in, key=FAKE_KEY)
    for path in ("/settings/cloud", "/", "/events/new", "/account/password", "/events/reregister"):
        body = logged_in.get(path, follow_redirects=True).get_data(as_text=True)
        assert FAKE_KEY not in body and "FAKEKEY" not in body, path
    assert "FAKEKEY" not in logged_in.get_cookie("ledsync_session").value
    for table in ("operation_log", "exception_log"):
        assert "FAKEKEY" not in json.dumps(db_rows(cfg, f"SELECT * FROM {table}"))


def test_blank_key_on_save_keeps_the_existing_key(app, cfg, logged_in, monkeypatch):
    for n in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY", "STORAGE_CONTAINER"):
        monkeypatch.delenv(n)
    post_settings(logged_in, key=FAKE_KEY)
    post_settings(logged_in, container="2027", key="")
    conn = connect(cfg.db_path)
    assert cs.load_cloud(conn).access_key == FAKE_KEY and cs.load_cloud(conn).container == "2027"
    conn.close()
    assert "Leave blank to keep it" in settings_page(logged_in)


def test_no_change_says_so(logged_in, monkeypatch):
    for n in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY", "STORAGE_CONTAINER"):
        monkeypatch.delenv(n)
    post_settings(logged_in, key=FAKE_KEY)
    post_settings(logged_in, key="")
    assert "Nothing changed" in settings_page(logged_in)


@pytest.mark.parametrize("kwargs,fragment", [
    (dict(account="ab"), "3 to 24 lower-case"),
    (dict(account="Has Space!"), "3 to 24 lower-case"),
    (dict(container="A B"), "3 to 63"),
    (dict(key="definitely not a key"), "does not look like"),
])
def test_invalid_settings_are_rejected_with_a_clear_message_keeping_what_was_typed(cfg, logged_in, kwargs, fragment):
    resp = post_settings(logged_in, **kwargs)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and fragment in html and 'role="alert"' in html
    assert "definitely not a key" not in html                                        # a typed key is never echoed back
    assert db_rows(cfg, "SELECT * FROM application_settings WHERE setting_name LIKE 'cloud_%'") == []   # nothing saved


def test_a_typed_but_invalid_key_is_not_reflected_into_the_page(logged_in):
    secret = "MY-SECRET-KEY-ATTEMPT-" + "x" * 30
    html = post_settings(logged_in, key=secret).get_data(as_text=True)
    assert secret not in html and "MY-SECRET" not in html


def test_settings_changes_are_audited_without_values(cfg, logged_in, monkeypatch):
    for n in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY", "STORAGE_CONTAINER"):
        monkeypatch.delenv(n)
    post_settings(logged_in, key=FAKE_KEY)
    [row] = db_rows(cfg, "SELECT message FROM operation_log WHERE operation = 'Settings Changed'")
    assert "storage account, container, access key changed" in row["message"]
    assert FAKE_KEY not in row["message"] and ACCOUNT not in row["message"]


def test_settings_forms_need_csrf(logged_in):
    assert logged_in.post("/settings/cloud", data={"account": ACCOUNT, "container": "2026", "access_key": "",
                                                   "action": "save"}).status_code == 403


def test_development_env_source_is_labelled(app, logged_in):
    html = settings_page(logged_in)                                                   # the fixture supplies env values
    assert "the process environment" in html and "Type a key here to save your own" in html


# --- Test Connection ----------------------------------------------------------------------------------------------------

def test_test_connection_success_reports_the_account_and_containers_without_saving(app, azure, cfg, logged_in):
    azure.containers.update({"2025", "2026", "archive"})
    resp = post_settings(logged_in, action="test")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and f"Connected to storage account {ACCOUNT}" in html
    assert "2025, 2026" in html and "Container 2026 found" in html
    assert [r["operation"] for r in db_rows(cfg, "SELECT operation FROM operation_log WHERE operation LIKE 'Cloud%'")] == ["Cloud Storage Test"]
    assert db_rows(cfg, "SELECT * FROM application_settings WHERE setting_name LIKE 'cloud_%'") == []   # nothing saved
    assert set(azure.calls) == {"list_containers"}                                                     # read-only listing


def test_test_connection_says_when_the_default_container_is_missing(app, azure, logged_in):
    azure.containers.add("2025")
    html = post_settings(logged_in, container="2031", action="test").get_data(as_text=True)
    assert "Container 2031 was not found" in html


def test_test_connection_uses_the_typed_key_without_saving_it(app, azure, cfg, logged_in):
    typed = "TYPEDKEY" + "T" * 78 + "=="
    azure.expected_key = typed
    resp = post_settings(logged_in, key=typed, action="test")
    assert resp.status_code == 200 and azure.given_key == typed
    assert typed not in resp.get_data(as_text=True)


def test_test_connection_with_a_wrong_key_is_a_clear_permission_error_and_is_logged(app, azure, cfg, logged_in):
    azure.expected_key = "SOMETHING" + "Z" * 79
    resp = post_settings(logged_in, action="test")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "refused the storage account key" in html and FAKE_KEY not in html
    assert [r["category"] for r in exc_rows(cfg)] == ["Permission"]
    assert db_rows(cfg, "SELECT status FROM operation_log WHERE operation = 'Cloud Storage Test'") == [{"status": "Failed"}]


def test_test_connection_offline_is_a_connectivity_error(app, azure, cfg, logged_in):
    azure.fail_with = ServiceRequestError("dns failure secret-request-id-abc123")
    resp = post_settings(logged_in, action="test")
    assert resp.status_code == 400 and "Could not reach Azure" in resp.get_data(as_text=True)
    assert "secret-request-id" not in resp.get_data(as_text=True)
    assert exc_rows(cfg)[0]["category"] == "Azure Storage connectivity"


def test_test_connection_without_any_key_asks_for_one(app, logged_in, monkeypatch):
    for n in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY", "STORAGE_CONTAINER"):
        monkeypatch.delenv(n)
    resp = post_settings(logged_in, action="test")
    assert resp.status_code == 400 and "Enter the access key" in resp.get_data(as_text=True)


def test_an_unknown_action_is_treated_as_save_never_as_something_else(logged_in, cfg, monkeypatch):
    for n in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY", "STORAGE_CONTAINER"):
        monkeypatch.delenv(n)
    resp = post_settings(logged_in, action="delete-everything", key=FAKE_KEY)
    assert resp.status_code == 302


# --- code-level guarantees ---------------------------------------------------------------------------------------------------

def test_only_the_settings_module_ever_reads_the_access_key_setting():
    src_root = ROOT / "ledsync"
    users = sorted(str(p.relative_to(src_root)).replace("\\", "/") for p in src_root.rglob("*.py")
                   if "cloud_access_key" in p.read_text(encoding="utf-8"))
    assert users == ["services/settings.py"]


def test_the_key_is_only_ever_passed_to_the_storage_client(app, azure, logged_in):
    """Nothing in the running app logs the key: the log call sites never format `access_key`."""
    for path in (ROOT / "ledsync").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(log|logger|logging)\.\w+\([^)]*access_key", text), path.name
        assert not re.search(r"oplog\.(record|add)\([^)]*access_key", text), path.name
