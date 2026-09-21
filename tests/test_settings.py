"""Phase 4 - Cloud Storage settings service (BRD 14) and the development .env fallback."""

import sys

import pytest
from fakes import ACCOUNT, FAKE_KEY

from ledsync import config as app_config
from ledsync.db import connect, init_db
from ledsync.services import settings as cs


@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def no_dev_environment(monkeypatch):
    """This module tests the settings service itself: start from a blank development environment."""
    for name in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY", "STORAGE_CONTAINER"):
        monkeypatch.delenv(name, raising=False)


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params)]


# --- validation ------------------------------------------------------------------------------------

@pytest.mark.parametrize("value,ok", [
    ("sasportspresentation", True), ("abc", True), ("a" * 24, True), ("  SASports  ", True),   # trimmed + lower-cased
    ("ab", False), ("a" * 25, False), ("has-dash", False), ("has space", False), ("", False),
    ("under_score", False), ("https://x.blob.core.windows.net", False), ("../x", False), ("a.b", False),
    ("sas\nports", False), ("ｓａｓ", False),
])
def test_account_name_validation(value, ok):
    if ok:
        assert cs.validate_account(value) == value.strip().lower()
    else:
        with pytest.raises(cs.SettingsError):
            cs.validate_account(value)


@pytest.mark.parametrize("value,ok", [
    ("2026", True), ("my-container", True), ("abc", True), ("", True),
    ("ab", False), ("a" * 64, False), ("-abc", False), ("abc-", False), ("a--b", False), ("Has Space", False),
    ("a/b", False), ("a_b", False), ("../x", False), ("ab\x00c", False),
])
def test_container_name_validation(value, ok):
    if ok:
        assert cs.validate_container(value) == value.strip().lower()
    else:
        with pytest.raises(cs.SettingsError):
            cs.validate_container(value)


@pytest.mark.parametrize("value,ok", [
    (FAKE_KEY, True), ("A" * 88, True), ("A" * 86 + "==", True), ("  " + FAKE_KEY + "  ", True),
    ("short", False), ("A" * 39, False), ("A" * 121, False), ("", False), ("has space " + "A" * 60, False),
    ("A" * 80 + "!!!!", False), ("A" * 80 + "===", False),
    # a pasted connection string is not a bare key (assembled at runtime so no key-shaped text sits in the repo)
    ("DefaultEndpointsProtocol=https;AccountName=x;" + "Account" + "Key=" + "A" * 88, False),
])
def test_access_key_validation(value, ok):
    if ok:
        assert cs.validate_key(value) == value.strip()
    else:
        with pytest.raises(cs.SettingsError):
            cs.validate_key(value)


def test_validation_messages_never_contain_the_value_entered():
    secret = "S3CRET-NOT-A-KEY!!" * 3
    with pytest.raises(cs.SettingsError) as exc:
        cs.validate_key(secret)
    assert secret not in str(exc.value) and "S3CRET" not in str(exc.value)


# --- save and load --------------------------------------------------------------------------------------

def test_defaults_when_nothing_is_saved(conn):
    s = cs.load_cloud(conn)
    assert (s.account, s.container, s.access_key, s.configured, s.has_key) == ("", "", "", False, False)


def test_saved_settings_are_reloaded(conn, cfg):
    changed = cs.save_cloud(conn, "  SasPortsPresentation ", " 2026 ", FAKE_KEY)
    assert changed == ["storage account", "container", "access key"]
    fresh = connect(cfg.db_path)                       # a new connection = as after an application restart
    try:
        s = cs.load_cloud(fresh)
    finally:
        fresh.close()
    assert (s.account, s.container, s.access_key) == ("sasportspresentation", "2026", FAKE_KEY)
    assert s.configured and s.has_key and s.blob_endpoint == f"https://{ACCOUNT}.blob.core.windows.net"


def test_blank_key_keeps_the_saved_key_and_a_new_one_replaces_it(conn):
    cs.save_cloud(conn, ACCOUNT, "2026", FAKE_KEY)
    assert cs.save_cloud(conn, ACCOUNT, "2027", "") == ["container"]
    assert cs.load_cloud(conn).access_key == FAKE_KEY
    assert cs.save_cloud(conn, ACCOUNT, "2027", None) == []
    new_key = "NEWKEY" + "B" * 80 + "=="
    assert cs.save_cloud(conn, ACCOUNT, "2027", new_key) == ["access key"]
    assert cs.load_cloud(conn).access_key == new_key


def test_container_may_be_left_blank(conn):
    cs.save_cloud(conn, ACCOUNT, "", FAKE_KEY)
    assert cs.load_cloud(conn).container == ""


def test_invalid_input_saves_nothing_at_all(conn):
    cs.save_cloud(conn, ACCOUNT, "2026", FAKE_KEY)
    for bad in (("ab", "2026", ""), (ACCOUNT, "A B", ""), (ACCOUNT, "2026", "not a key")):
        with pytest.raises(cs.SettingsError):
            cs.save_cloud(conn, *bad)
    s = cs.load_cloud(conn)
    assert (s.account, s.container, s.access_key) == (ACCOUNT, "2026", FAKE_KEY)


def test_partial_failure_rolls_back_so_a_bad_key_does_not_change_the_account(conn):
    with pytest.raises(cs.SettingsError):
        cs.save_cloud(conn, "otheraccount", "2030", "bad key")
    assert cs.load_cloud(conn).account == ""


def test_the_settings_change_and_its_audit_row_are_one_transaction(conn, monkeypatch):
    def boom(*a, **k):
        raise __import__("sqlite3").OperationalError("disk I/O error")

    monkeypatch.setattr(cs.oplog, "add", boom)
    with pytest.raises(cs.SettingsError, match="could not be saved"):
        cs.save_cloud(conn, ACCOUNT, "2026", FAKE_KEY)
    assert cs.load_cloud(conn).account == "" and rows(conn, "SELECT * FROM application_settings") == []


def test_audit_row_names_the_fields_changed_but_never_a_value(conn):
    cs.save_cloud(conn, ACCOUNT, "2026", FAKE_KEY)
    cs.save_cloud(conn, ACCOUNT, "2026", "")
    msgs = [r["message"] for r in rows(conn, "SELECT message FROM operation_log WHERE operation = 'Settings Changed'")]
    assert msgs == ["Cloud storage settings saved (storage account, container, access key changed).",
                    "Cloud storage settings saved (no change)."]
    blob = " ".join(msgs)
    assert FAKE_KEY not in blob and "FAKEKEY" not in blob and "2026" not in blob and ACCOUNT not in blob


def test_the_key_is_never_in_a_repr_or_str_of_the_settings(conn):
    cs.save_cloud(conn, ACCOUNT, "2026", FAKE_KEY)
    s = cs.load_cloud(conn)
    assert FAKE_KEY not in repr(s) and FAKE_KEY not in str(s) and "FAKEKEY" not in repr(s)


# --- ownership: this module touches only its own keys ------------------------------------------------------

def test_it_refuses_to_read_or_write_any_key_it_does_not_own(conn):
    for key in ("admin_password", "admin_username", "anything_else"):
        with pytest.raises(PermissionError):
            cs._get(conn, key)
        with pytest.raises(PermissionError):
            cs._put(conn, key, "x")


def test_saving_settings_never_disturbs_the_admin_credential(conn):
    from ledsync.services import auth

    auth.seed_admin(conn)
    cs.save_cloud(conn, ACCOUNT, "2026", FAKE_KEY)
    assert auth.check_credentials(conn, "admin", "Admin@123")
    admin_rows = rows(conn, "SELECT setting_name FROM application_settings WHERE setting_name LIKE 'admin_%'")
    assert len(admin_rows) == 2


# --- development fallback (.env / environment) -------------------------------------------------------------------

def test_dotenv_parser_handles_comments_quotes_and_bad_lines(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# a comment\n\nSTORAGE_ACCOUNT_NAME=sasportspresentation\n"
                 "STORAGE_ACCOUNT_KEY=\"" + FAKE_KEY + "\"\nSTORAGE_CONTAINER='2026'\n"
                 "bad line without equals\nBAD-NAME=x\n=novalue\nEMPTY=\n", encoding="utf-8-sig")
    values = app_config.read_dotenv(f)
    assert values["STORAGE_ACCOUNT_NAME"] == "sasportspresentation"
    assert values["STORAGE_ACCOUNT_KEY"] == FAKE_KEY and values["STORAGE_CONTAINER"] == "2026"
    assert "BAD-NAME" not in values and "" not in values and values["EMPTY"] == ""


def test_dotenv_missing_or_unreadable_is_just_empty(tmp_path):
    assert app_config.read_dotenv(tmp_path / "nope.env") == {}
    assert app_config.read_dotenv(tmp_path) == {}            # a directory


def test_an_installed_build_never_reads_a_dotenv(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("STORAGE_ACCOUNT_KEY=" + FAKE_KEY, encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert app_config.read_dotenv(f) == {}


def test_environment_variables_win_over_the_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ACCOUNT_NAME", "fromenv")
    assert app_config.dev_setting("STORAGE_ACCOUNT_NAME", {"STORAGE_ACCOUNT_NAME": "fromfile"}) == "fromenv"


def test_unsaved_fields_fall_back_to_the_dev_environment_per_field(conn):
    dotenv = {"STORAGE_ACCOUNT_NAME": "DevAccount", "STORAGE_ACCOUNT_KEY": FAKE_KEY, "STORAGE_CONTAINER": "2026"}
    s = cs.load_cloud(conn, dotenv)
    assert (s.account, s.container, s.access_key) == ("devaccount", "2026", FAKE_KEY)
    assert s.account_from_dev_env and s.key_from_dev_env and s.configured
    # once a value is saved in Settings it wins, field by field
    cs.save_cloud(conn, "savedaccount", "", None)
    s = cs.load_cloud(conn, dotenv)
    assert s.account == "savedaccount" and not s.account_from_dev_env and s.key_from_dev_env


def test_dev_key_is_still_never_in_the_repr(conn):
    s = cs.load_cloud(conn, {"STORAGE_ACCOUNT_NAME": ACCOUNT, "STORAGE_ACCOUNT_KEY": FAKE_KEY})
    assert FAKE_KEY not in repr(s)


def test_env_example_documents_the_variables_with_no_real_value():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    assert "STORAGE_ACCOUNT_NAME" in text and "STORAGE_ACCOUNT_KEY" in text
    assert not any(line.startswith("STORAGE_ACCOUNT_KEY=") and len(line.split("=", 1)[1].strip()) > 0
                   for line in text.splitlines())
