"""Phase 4 architect-review findings: each has a test that failed before the fix."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from azure.core.exceptions import DecodeError
from conftest import csrf_from, db_rows, serve_event
from fakes import ACCOUNT, FAKE_KEY, FakeBlobService, http_error

from ledsync import config as app_config
from ledsync.db import connect
from ledsync.services import cloud, exceptions
from ledsync.services import registration as reg
from ledsync.services import settings as cs
from ledsync.services import storage as st
from ledsync.services.storage import AzureReadOnlyStorage, EventLocation, StorageError, map_error

ROOT = Path(__file__).resolve().parent.parent
FILES = ROOT / "docs" / "testfiles" / "phase3"
LOC = EventLocation("2026", "1000 - Star contender Doha")
BASE = f"https://{ACCOUNT}.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha"


def make(fake=None, key=FAKE_KEY):
    fake = fake or FakeBlobService()
    return AzureReadOnlyStorage(ACCOUNT, key, service_factory=fake.factory), fake


def cfg_with_url(url: str) -> reg.EventConfig:
    obj = json.loads((FILES / "1000_valid.json").read_text())
    obj["eventStorageUrl"] = url
    return reg.parse_event_config(json.dumps(obj).encode())


def register(client, event_id):
    return client.post("/events/new", data={"event_id": event_id, "csrf_token": csrf_from(client, "/events/new")})


# ================= 1. storage-address verification: no crash, strict =================

@pytest.mark.parametrize("url", [
    f"https://{ACCOUNT}.blob.core.windows.net:abc/2026/1000%20-%20Star%20contender%20Doha",     # crashed with ValueError
    f"https://{ACCOUNT}.blob.core.windows.net:99999/2026/1000%20-%20Star%20contender%20Doha",
    f"https://{ACCOUNT}.blob.core.windows.net:-1/2026/1000%20-%20Star%20contender%20Doha",
    f"https://{ACCOUNT}.blob.core.windows.net:/2026/1000%20-%20Star%20contender%20Doha/x",
])
def test_a_malformed_port_is_a_clean_rejection_never_a_500(app, azure, cfg, logged_in, url):
    obj = json.loads((FILES / "1000_valid.json").read_text())
    obj["eventStorageUrl"] = url
    serve_event(azure, obj, folder="1000 - Star contender Doha", fix_url=False)
    resp = register(logged_in, "1000")
    assert resp.status_code == 400 and "Traceback" not in resp.get_data(as_text=True)
    assert db_rows(cfg, "SELECT * FROM events") == []
    assert db_rows(cfg, "SELECT category FROM exception_log")            # logged (was: a 500 with no row)


@pytest.mark.parametrize("url", [
    BASE + "?sv=2020&sig=SECRET",                                        # accepted before; persisted a credential
    BASE + "#fragment",
    BASE + ";x=1",
    f"https://{ACCOUNT}.blob.core.windows.net/2026%2F1000%20-%20Star%20contender%20Doha",       # decoded %2F
    f"https://{ACCOUNT}.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha//",
    f"https://{ACCOUNT}.blob.core.windows.net//2026/1000%20-%20Star%20contender%20Doha",
    f"https://{ACCOUNT}.blob.core.windows.net/2026/%2E%2E/2026/1000%20-%20Star%20contender%20Doha",
    f"https://{ACCOUNT}.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha%5Cx",
])
def test_loose_storage_addresses_are_refused(url):
    """Either the parser or the verifier must refuse - and never persist a query string."""
    with pytest.raises(reg.RegistrationError):
        cloud.verify_storage_location(cfg_with_url(url), ACCOUNT, LOC)


def test_a_query_or_fragment_is_refused_already_at_parse_time_so_it_is_never_stored():
    for suffix in ("?sig=SECRET", "?x", "#f"):
        with pytest.raises(reg.RegistrationError, match="plain https address"):
            cfg_with_url(BASE + suffix)


@pytest.mark.parametrize("url", [BASE, BASE + "/", BASE.replace("sasportspresentation", "SASPORTSPRESENTATION"),
                                 BASE.replace(".net/", ".net:443/")])
def test_legitimate_spellings_still_verify(url):
    cloud.verify_storage_location(cfg_with_url(url), ACCOUNT, LOC)


def test_a_secret_query_never_reaches_the_database(app, azure, cfg, logged_in):
    obj = json.loads((FILES / "1000_valid.json").read_text())
    obj["eventStorageUrl"] = BASE + "?sv=2020&sig=TOPSECRETSIGNATURE"
    serve_event(azure, obj, folder="1000 - Star contender Doha", fix_url=False)
    register(logged_in, "1000")
    everything = json.dumps({t: db_rows(cfg, f"SELECT * FROM {t}") for t in ("events", "exception_log", "operation_log")})
    assert "TOPSECRETSIGNATURE" not in everything


# ================= 2. Azure lookup honours the case-insensitive Event ID rule =================

def test_event_folder_is_found_whatever_case_the_id_is_typed_in():
    storage, fake = make()
    fake.put("2026", "EVT9 - Second Test Event/_GUID.json", b"{}")
    for typed in ("EVT9", "evt9", "Evt9", "eVt9"):
        assert storage.find_event(typed, "2026").folder == "EVT9 - Second Test Event"


def test_case_insensitive_matching_still_respects_the_exact_id_boundary():
    storage, fake = make()
    fake.put("2026", "EVT90 - Other/_GUID.json", b"{}")
    with pytest.raises(StorageError) as exc:
        storage.find_event("evt9", "2026")
    assert exc.value.category == exceptions.MISSING_FOLDER


def test_folders_differing_only_by_case_are_ambiguous_not_guessed():
    storage, fake = make()
    fake.put("2026", "EVT9 - A/_GUID.json", b"{}")
    fake.put("2026", "evt9 - B/_GUID.json", b"{}")
    with pytest.raises(StorageError) as exc:
        storage.find_event("Evt9", "2026")
    assert exc.value.category == exceptions.CONFIGURATION


def test_the_top_level_scan_is_capped(monkeypatch):
    storage, fake = make()
    for i in range(20):
        fake.put("2026", f"{i + 100} - Event {i}/_GUID.json", b"{}")
    monkeypatch.setattr(st, "MAX_TOP_LEVEL_FOLDERS", 5)
    with pytest.raises(StorageError, match="too many folders"):
        storage.find_event("999", "2026")


def test_leading_zeros_remain_significant_in_the_lookup():
    storage, fake = make()
    fake.put("2026", "1000 - A/_GUID.json", b"{}")
    with pytest.raises(StorageError):
        storage.find_event("01000", "2026")


# ================= 3. Test Connection is honest about not saving =================

def post_settings(client, action="test", key="", account=ACCOUNT, container="2026"):
    return client.post("/settings/cloud", data={"account": account, "container": container, "access_key": key,
                                               "action": action, "csrf_token": csrf_from(client, "/settings/cloud")})


def test_a_successful_test_says_nothing_was_saved(app, azure, logged_in):
    azure.containers.add("2026")
    html = post_settings(logged_in).get_data(as_text=True)
    assert "Nothing has been saved." in html


def test_a_successful_test_with_a_typed_key_tells_the_operator_to_type_it_again_and_save(app, azure, logged_in):
    typed = "TYPEDKEY" + "T" * 78 + "=="
    azure.expected_key = typed
    html = post_settings(logged_in, key=typed).get_data(as_text=True)
    assert "type the key again and press Save" in html and typed not in html


# ================= 4. the .env / environment fallback is development-only and validated =================

@pytest.fixture
def no_env(monkeypatch):
    for n in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY", "STORAGE_CONTAINER"):
        monkeypatch.delenv(n, raising=False)


def test_a_frozen_build_ignores_the_environment_as_well_as_the_dotenv(monkeypatch, no_env):
    monkeypatch.setenv("STORAGE_ACCOUNT_KEY", FAKE_KEY)
    assert app_config.dev_setting("STORAGE_ACCOUNT_KEY") == FAKE_KEY
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert app_config.dev_setting("STORAGE_ACCOUNT_KEY") == "" and app_config.dev_setting_with_source("X") == ("", "")


@pytest.mark.parametrize("name", ["evil.com/x#", "attacker.example", "ab", "has space", "under_score", "a" * 30])
def test_a_hostile_or_malformed_fallback_account_is_ignored_not_used(cfg, monkeypatch, no_env, name):
    from ledsync.db import init_db

    init_db(cfg.db_path)
    monkeypatch.setenv("STORAGE_ACCOUNT_NAME", name)
    monkeypatch.setenv("STORAGE_ACCOUNT_KEY", FAKE_KEY)
    conn = connect(cfg.db_path)
    s = cs.load_cloud(conn)
    conn.close()
    assert s.account == "" and not s.configured                      # never builds a URL to that host


def test_fallback_container_and_key_are_validated_too(cfg, monkeypatch, no_env):
    from ledsync.db import init_db

    init_db(cfg.db_path)
    monkeypatch.setenv("STORAGE_ACCOUNT_NAME", ACCOUNT)
    monkeypatch.setenv("STORAGE_CONTAINER", "../evil")
    monkeypatch.setenv("STORAGE_ACCOUNT_KEY", "not a key")
    conn = connect(cfg.db_path)
    s = cs.load_cloud(conn)
    conn.close()
    assert (s.account, s.container, s.access_key) == (ACCOUNT, "", "")


def test_the_source_is_named_correctly_environment_or_dotenv(cfg, monkeypatch, no_env, tmp_path):
    from ledsync.db import init_db

    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    monkeypatch.setenv("STORAGE_ACCOUNT_NAME", ACCOUNT)
    assert cs.load_cloud(conn).account_source == "the process environment"
    monkeypatch.delenv("STORAGE_ACCOUNT_NAME")
    envfile = tmp_path / ".env"
    envfile.write_text(f"STORAGE_ACCOUNT_NAME={ACCOUNT}\n", encoding="utf-8")
    monkeypatch.setattr(app_config, "DOTENV_PATH", envfile)
    assert cs.load_cloud(conn).account_source == "the development .env file"
    conn.close()


# ================= 5. a key pasted into the wrong field is never echoed =================

@pytest.mark.parametrize("text,blanked", [
    (FAKE_KEY, True), (FAKE_KEY[:60], True), ("A" * 40, True), ("Ab+/=" * 10, True),
    ("1000", False), ("EVT9", False), ("2026", False), ("my-container", False), ("sasportspresentation", False),
    ("a" * 39, False), ("", False), ("has space " + "A" * 60, False),
    ("a" * 45, False), ("2026-archive-of-all-of-the-old-events-here", False),        # a long but plain lower-case name
])
def test_redact_if_secret_like(text, blanked):
    assert cs.redact_if_secret_like(text) == ("" if blanked else text)


def test_a_key_pasted_into_the_event_id_field_is_not_reflected(app, azure, logged_in):
    pasted = FAKE_KEY[:50]
    resp = logged_in.post("/events/new", data={"event_id": pasted, "csrf_token": csrf_from(logged_in, "/events/new")})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "does not look like an Event ID" in html
    assert pasted not in html and "FAKEKEY" not in html
    assert azure.calls == []                                # and Azure was never contacted with it


@pytest.mark.parametrize("field", ["account", "container"])
def test_a_key_pasted_into_a_settings_field_is_not_reflected(app, logged_in, field):
    pasted = FAKE_KEY[:58]
    resp = post_settings(logged_in, action="save", **{field: pasted})
    assert resp.status_code == 400 and pasted not in resp.get_data(as_text=True) and "FAKEKEY" not in resp.get_data(as_text=True)


# ================= 6. an offline venue fails fast =================

def test_retry_and_timeouts_are_bounded_for_an_offline_venue():
    assert st.RETRY_TOTAL == 1 and st.CONNECT_TIMEOUT <= 8 and st.READ_TIMEOUT <= 20
    key = "dGVzdC1rZXktbm90LXJlYWw=" * 4
    client = AzureReadOnlyStorage._default_service(f"https://{ACCOUNT}.blob.core.windows.net", key)
    policy = client._config.retry_policy                       # the SDK's actual configured policy
    assert policy.total_retries == 1
    # worst case ~ (connect timeout + backoff) x 2 attempts; must stay well under the old ~37 s
    assert (st.CONNECT_TIMEOUT + 3) * (st.RETRY_TOTAL + 1) <= 25


# ================= 8. bfcache =================

def test_javascript_resets_busy_forms_when_a_page_is_restored_from_cache(launched):
    js = launched.get("/static/js/app.js").get_data(as_text=True)
    assert "pageshow" in js and "event.persisted" in js and "disabled = false" in js


# ================= 9. SDK log noise =================

def test_the_azure_sdk_is_not_allowed_to_log_every_request(cfg):
    import logging

    from ledsync import logging_setup

    logging_setup.setup_logging(cfg.data_dir)
    assert logging.getLogger("azure").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("azure.core.pipeline.policies.http_logging_policy").getEffectiveLevel() >= logging.WARNING


# ================= 10. read_blob takes a validated location + relative path =================

@pytest.mark.parametrize("rel", ["../other/_GUID.json", "a/../../b", "/abs", "a//b", "a/./b", "", "a\\b", "a\x00b",
                                 "x" * 1025, "..", ".", "a/"])
def test_unsafe_relative_paths_are_refused(rel):
    storage, fake = make()
    fake.put("2026", "1000 - Star contender Doha/_GUID.json", b"{}")
    with pytest.raises(StorageError) as exc:
        storage.read_blob(LOC, rel, 100)
    assert exc.value.category == exceptions.CONFIGURATION and fake.downloads == []


def test_safe_relative_paths_map_onto_the_event_folder():
    assert LOC.blob_path("Table 1/inner/a b.png") == "1000 - Star contender Doha/Table 1/inner/a b.png"
    assert LOC.blob_url(ACCOUNT, "Table 1/inner/a b.png").endswith("/Table%201/inner/a%20b.png")


def test_a_missing_non_guid_file_gets_its_own_message():
    storage, fake = make()
    fake.put("2026", "1000 - Star contender Doha/_GUID.json", b"{}")
    with pytest.raises(StorageError) as exc:
        storage.read_blob(LOC, "_ledassetschangelog.csv", 100)
    assert "no file _ledassetschangelog.csv" in exc.value.message and "_GUID.json" not in exc.value.message


def test_an_empty_blob_is_reported_as_an_empty_file_not_a_generic_error():
    storage, fake = make()
    fake.put("2026", "1000 - Star contender Doha/_GUID.json", b"")
    fake.fail_with = http_error(416)
    assert storage.read_blob(LOC, "_GUID.json", 100) == b""
    with pytest.raises(reg.RegistrationError, match="empty"):
        reg.parse_event_config(b"")


# ================= notes done alongside =================

def test_pages_receive_a_key_less_view_of_the_settings(app, logged_in):
    from flask import template_rendered

    seen = []

    def capture(sender, template, context, **extra):
        if template.name == "settings_cloud.html":
            seen.append(context["saved"])

    template_rendered.connect(capture, app)
    try:
        logged_in.get("/settings/cloud")
    finally:
        template_rendered.disconnect(capture, app)
    assert seen and not hasattr(seen[0], "access_key") and FAKE_KEY not in repr(seen[0])
    assert seen[0].has_key is True


def test_year_containers_must_be_exactly_four_ascii_digits():
    storage, fake = make()
    for name in ("２０２５", "2026\n", "20265", "202", "abcd"):
        fake.containers.add(name)
    fake.containers.add("2025")
    assert storage.test_connection("").year_containers == ("2025",)


@pytest.mark.parametrize("value", ["A" * 43, "A" * 89, "A" * 41 + "=", "A" * 45])
def test_keys_that_are_not_valid_base64_are_refused_up_front(value):
    with pytest.raises(cs.SettingsError):
        cs.validate_key(value)


def test_a_proxy_or_sign_in_page_reply_gets_its_own_message():
    err = map_error(DecodeError("Response is not JSON: <html>Please sign in</html> secret-request-id-abc123"), "testing")
    assert err.category == exceptions.DOWNLOAD and "proxy" in err.message and "secret-request-id" not in err.message


def test_settings_cloud_and_storage_layers_do_not_need_flask():
    """Phase 12: a headless scheduled run must be able to use them without any web framework."""
    code = ("import sys; import ledsync.services.settings, ledsync.services.cloud, ledsync.services.storage; "
            "print('flask' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)
    assert out.stdout.strip() == "False"


def test_from_settings_builds_the_real_read_only_client_offline():
    s = cs.CloudSettings(account=ACCOUNT, container="2026", access_key="dGVzdC1rZXktbm90LXJlYWw=" * 4)
    assert isinstance(st.from_settings(s), AzureReadOnlyStorage)
