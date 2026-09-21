"""Phase 3 - event registration and GUID validation (BRD 8, 9, 10): the service layer."""

import copy
import json
from pathlib import Path

import pytest

from ledsync.db import connect, init_db
from ledsync.services import exceptions
from ledsync.services import registration as reg

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "reference-docs" / "samplefiles-webassetmgmt" / "_GUID.json"
REAL = json.loads(SAMPLE.read_text(encoding="utf-8"))
GUID_1000 = "eb0153b9-b74a-45e8-9f0f-b2c1a1b7b459"
NEW_GUID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def blob(**overrides) -> bytes:
    obj = copy.deepcopy(REAL)
    for key, value in overrides.items():
        if value is ...:
            obj.pop(key, None)
        else:
            obj[key] = value
    return json.dumps(obj).encode()


@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    yield c
    c.close()


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params)]


# --- the real exported file --------------------------------------------------------

def test_the_real_exported_test_event_parses():
    cfg = reg.parse_event_config(SAMPLE.read_bytes())
    assert (cfg.event_id, cfg.event_name) == ("1000", "Star contender Doha")
    assert cfg.guid == GUID_1000
    assert cfg.export_timestamp == "2026-09-18T07:02:26.380Z"
    assert cfg.storage_url.startswith("https://sasportspresentation.blob.core.windows.net/2026/")
    assert [(t.number, t.inner, t.outer, t.main) for t in cfg.tables] == [
        (1, True, True, True), (2, True, False, False)]           # BRD 11: exactly what the file says


def test_utf8_bom_is_tolerated():
    assert reg.parse_event_config(b"\xef\xbb\xbf" + SAMPLE.read_bytes()).event_id == "1000"


def test_unknown_extra_fields_are_ignored_for_forward_compatibility():
    assert reg.parse_event_config(blob(futureField={"a": 1})).event_id == "1000"


def test_numeric_event_id_is_accepted_as_text():
    assert reg.parse_event_config(blob(eventId=1000)).event_id == "1000"


def test_missing_or_bad_export_timestamp_is_not_fatal():
    assert reg.parse_event_config(blob(exportTimestamp=...)).export_timestamp is None
    assert reg.parse_event_config(blob(exportTimestamp="whenever")).export_timestamp is None
    assert reg.parse_event_config(blob(exportTimestamp="1969-01-01T00:00:00Z")).export_timestamp is None


# --- GUID helpers ---------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (GUID_1000, GUID_1000),
    (GUID_1000.upper(), GUID_1000),
    ("  " + GUID_1000 + "  ", GUID_1000),
    ("{" + GUID_1000.upper() + "}", GUID_1000),
    (GUID_1000.replace("-", ""), None),            # must be the hyphenated form
    ("urn:uuid:" + GUID_1000, None),
    (GUID_1000[:-1], None),
    (GUID_1000 + "0", None),
    ("00000000-0000-0000-0000-000000000000", None),  # nil GUID = a blank placeholder
    ("zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz", None),
    ("", None), (None, None), (123, None), ([GUID_1000], None),
])
def test_normalise_guid(value, expected):
    assert reg.normalise_guid(value) == expected


def test_validate_guid_matches_case_and_brace_insensitively_and_fails_closed():
    assert reg.validate_guid(GUID_1000, GUID_1000.upper())
    assert reg.validate_guid("{" + GUID_1000 + "}", GUID_1000)
    assert not reg.validate_guid(GUID_1000, NEW_GUID)
    for bad in (None, "", "garbage", "00000000-0000-0000-0000-000000000000"):
        assert not reg.validate_guid(bad, GUID_1000)
        assert not reg.validate_guid(GUID_1000, bad)
        assert not reg.validate_guid(bad, bad)


# --- strict parsing: every malformed shape is rejected with a safe message -------------

def _err(data: bytes) -> reg.RegistrationError:
    with pytest.raises(reg.RegistrationError) as exc:
        reg.parse_event_config(data)
    return exc.value


@pytest.mark.parametrize("data,fragment", [
    (b"", "empty"),
    (b"   \n  ", "empty"),
    (b"not json at all", "valid JSON"),
    (b'{"eventId": "1"', "valid JSON"),
    (b"\xff\xfe\x00bad", "UTF-8"),
    (b"[1, 2, 3]", "JSON object"),
    (b'"just a string"', "JSON object"),
    (b"null", "JSON object"),
    (b'{"a": NaN}', "valid JSON"),
    (b'{"a": Infinity}', "valid JSON"),
    (b'{"eventId": "1", "eventId": "2"}', "valid JSON"),        # duplicate keys are ambiguous
    (b"[" * 50_000, "valid JSON"),                              # deep nesting (under the size cap) must not crash us
    (b"[" * 100_000, "too large"),
    (b"x" * (reg.MAX_CONFIG_BYTES + 1), "too large"),
], ids=lambda v: f"{type(v).__name__}{len(v)}")
def test_unusable_files_are_rejected(data, fragment):
    err = _err(data)
    assert err.category == exceptions.INVALID_CONFIGURATION and fragment in err.message


@pytest.mark.parametrize("field", ["eventId", "eventName", "eventStorageUrl", "exportGuid", "tables"])
def test_each_required_field_is_required(field):
    err = _err(blob(**{field: ...}))
    assert err.category == exceptions.INVALID_CONFIGURATION
    assert field in err.message or "table" in err.message


@pytest.mark.parametrize("override,fragment", [
    (dict(eventId=""), "eventId"),
    (dict(eventId="../x"), "Event ID"),
    (dict(eventId="a" * 51), "too long"),
    (dict(eventId=True), "eventId"),
    (dict(eventId=["1000"]), "eventId"),
    (dict(eventName="   "), "eventName"),
    (dict(eventName="x" * 201), "too long"),
    (dict(eventName="bad\x07name"), "not allowed"),
    (dict(eventName=123.5), "eventName"),
    (dict(eventStorageUrl="http://sasportspresentation.blob.core.windows.net/x"), "https"),
    (dict(eventStorageUrl="https://user:pw@sasportspresentation.blob.core.windows.net/x"), "https"),
    (dict(eventStorageUrl="ftp://host/x"), "https"),
    (dict(eventStorageUrl="https://"), "https"),
    (dict(eventStorageUrl="x" * 2049), "too long"),
    (dict(exportGuid="not-a-guid"), "GUID"),
    (dict(exportGuid="00000000-0000-0000-0000-000000000000"), "GUID"),
    (dict(exportGuid=12345), "GUID"),
    (dict(tables=[]), "at least one table"),
    (dict(tables="Table 1"), "at least one table"),
    (dict(tables=[1, 2]), "object"),
    (dict(tables=[{"tableNumber": 1, "innerLed": True, "outerLed": True}]), "mainLed"),
    (dict(tables=[{"tableNumber": 1, "innerLed": "yes", "outerLed": True, "mainLed": True}]), "innerLed"),
    (dict(tables=[{"tableNumber": 1, "innerLed": 1, "outerLed": 0, "mainLed": 1}]), "innerLed"),
    (dict(tables=[{"tableNumber": "1", "innerLed": True, "outerLed": True, "mainLed": True}]), "tableNumber"),
    (dict(tables=[{"tableNumber": True, "innerLed": True, "outerLed": True, "mainLed": True}]), "tableNumber"),
    (dict(tables=[{"tableNumber": 0, "innerLed": True, "outerLed": True, "mainLed": True}]), "tableNumber"),
    (dict(tables=[{"tableNumber": -3, "innerLed": True, "outerLed": True, "mainLed": True}]), "tableNumber"),
    (dict(tables=[{"tableNumber": 1.5, "innerLed": True, "outerLed": True, "mainLed": True}]), "tableNumber"),
    (dict(tables=[{"tableNumber": 10_000, "innerLed": True, "outerLed": True, "mainLed": True}]), "tableNumber"),
    (dict(tables=[{"tableNumber": 1, "innerLed": True, "outerLed": True, "mainLed": True}] * 2), "more than once"),
], ids=lambda v: (list(v)[0] if isinstance(v, dict) else v)[:24] if isinstance(v, (dict, str)) else "v")
def test_malformed_fields_are_rejected(override, fragment):
    err = _err(blob(**override))
    assert err.category == exceptions.INVALID_CONFIGURATION
    assert fragment in err.message


def test_table_count_safety_cap():
    def tables(n):
        return [{"tableNumber": i, "innerLed": True, "outerLed": False, "mainLed": False} for i in range(1, n + 1)]

    assert len(reg.parse_event_config(blob(tables=tables(reg.MAX_TABLES))).tables) == reg.MAX_TABLES
    assert "more than" in _err(blob(tables=tables(reg.MAX_TABLES + 1))).message


def test_a_table_with_no_led_type_enabled_is_accepted_exactly_as_the_file_says():
    cfg = reg.parse_event_config(blob(tables=[{"tableNumber": 1, "innerLed": False, "outerLed": False, "mainLed": False}]))
    assert not any((cfg.tables[0].inner, cfg.tables[0].outer, cfg.tables[0].main))


def test_error_messages_never_echo_file_content():
    hostile = "<script>alert('x')</script>" + "A" * 30
    for override in (dict(eventName="x" * 500 + hostile), dict(exportGuid=hostile),
                     dict(eventStorageUrl=hostile), dict(tables=hostile)):
        assert hostile not in _err(blob(**override)).message


# --- Event ID typed by the operator -----------------------------------------------------

@pytest.mark.parametrize("text,ok", [
    ("1000", True), ("EVENT001", True), ("a.b-c_d", True), ("  1000  ", True),
    ("", False), ("   ", False), ("../etc", False), ("1000/x", False), ("a b", False),
    ("-1000", False), (".hidden", False), ("x" * 51, False), ("1000\n", True), ("１０００", False),
    ("<script>", False), ("1000;DROP", False),
])
def test_event_id_input_validation(text, ok):
    if ok:
        assert reg.validate_event_id_input(text) == text.strip()
    else:
        with pytest.raises(reg.InputError):
            reg.validate_event_id_input(text)


def test_input_errors_are_not_logged_as_exceptions():
    assert reg.InputError("typo").log is False


# --- registration (Step 3.1) ------------------------------------------------------------

def test_a_valid_file_creates_the_events_row(conn):
    cfg = reg.parse_event_config(SAMPLE.read_bytes())
    assert reg.register_event(conn, "1000", cfg, "Local test file: _GUID.json") == "registered"
    [row] = rows(conn, "SELECT * FROM events")
    assert row["event_id"] == "1000" and row["event_name"] == "Star contender Doha"
    assert row["event_guid"] == GUID_1000
    assert row["status"] == "Registered"
    assert row["configuration_file"] == "Local test file: _GUID.json"
    assert row["configuration_json"] == SAMPLE.read_bytes().decode("utf-8")   # the file, kept verbatim
    assert row["last_updated"] == "2026-09-18T07:02:26.380Z"                   # owner: export time
    assert row["last_download"] is None and row["last_sync"] is None
    [op] = rows(conn, "SELECT operation, status, event_id, message FROM operation_log")
    assert (op["operation"], op["status"], op["event_id"]) == ("Event Registered", "Success", "1000")
    assert "2 table" in op["message"]


def test_event_id_in_the_file_must_match_what_was_entered(conn):
    cfg = reg.parse_event_config(SAMPLE.read_bytes())
    with pytest.raises(reg.RegistrationError) as exc:
        reg.register_event(conn, "2000", cfg, "src")
    assert exc.value.category == exceptions.CONFIGURATION and "1000" in exc.value.message and "2000" in exc.value.message
    assert rows(conn, "SELECT * FROM events") == []


def test_same_event_same_guid_is_a_harmless_no_op(conn):
    cfg = reg.parse_event_config(SAMPLE.read_bytes())
    reg.register_event(conn, "1000", cfg, "src")
    assert reg.register_event(conn, "1000", cfg, "other src") == "already_registered"
    [row] = rows(conn, "SELECT configuration_file FROM events")
    assert row["configuration_file"] == "src"                                   # untouched
    assert len(rows(conn, "SELECT * FROM operation_log")) == 1


def test_existing_event_with_a_different_guid_is_rejected_step_3_2(conn):
    reg.register_event(conn, "1000", reg.parse_event_config(SAMPLE.read_bytes()), "src")
    other = reg.parse_event_config(blob(exportGuid=NEW_GUID))
    with pytest.raises(reg.GuidMismatch) as exc:
        reg.register_event(conn, "1000", other, "src2")
    err = exc.value
    assert err.category == exceptions.GUID_VALIDATION
    assert (err.recorded, err.incoming) == (GUID_1000, NEW_GUID)
    assert GUID_1000 in err.message and NEW_GUID in err.message
    [row] = rows(conn, "SELECT event_guid FROM events")
    assert row["event_guid"] == GUID_1000                                        # nothing changed


def test_a_guid_may_not_be_reused_by_a_different_event(conn):
    reg.register_event(conn, "1000", reg.parse_event_config(SAMPLE.read_bytes()), "src")
    clone = reg.parse_event_config(blob(eventId="2001", eventName="Clone"))       # same GUID, other event
    with pytest.raises(reg.RegistrationError) as exc:
        reg.register_event(conn, "2001", clone, "src")
    assert exc.value.category == exceptions.GUID_VALIDATION and "1000" in exc.value.message
    assert [r["event_id"] for r in rows(conn, "SELECT event_id FROM events")] == ["1000"]


def test_guid_reuse_check_ignores_case_and_formatting_in_stored_values(conn):
    conn.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('9', 'Hand made', ?)",
                 ("{" + GUID_1000.upper() + "}",))
    conn.commit()
    with pytest.raises(reg.RegistrationError):
        reg.register_event(conn, "1000", reg.parse_event_config(SAMPLE.read_bytes()), "src")


def test_hand_inserted_event_with_no_guid_cannot_be_matched(conn):
    conn.execute("INSERT INTO events (event_id, event_name) VALUES ('1000', 'No guid recorded')")
    conn.commit()
    with pytest.raises(reg.GuidMismatch) as exc:
        reg.register_event(conn, "1000", reg.parse_event_config(SAMPLE.read_bytes()), "src")
    assert exc.value.recorded is None                                            # fails closed


def test_two_events_can_be_registered_side_by_side(conn):
    reg.register_event(conn, "1000", reg.parse_event_config(SAMPLE.read_bytes()), "a")
    second = reg.parse_event_config(blob(eventId="2000", eventName="Second", exportGuid=NEW_GUID))
    assert reg.register_event(conn, "2000", second, "b") == "registered"
    assert sorted(r["event_id"] for r in rows(conn, "SELECT event_id FROM events")) == ["1000", "2000"]


# --- rejections are logged (Step 3.2 validation) ---------------------------------------------

def test_a_guid_rejection_is_recorded_in_the_exception_log(conn):
    reg.register_event(conn, "1000", reg.parse_event_config(SAMPLE.read_bytes()), "src")
    try:
        reg.register_event(conn, "1000", reg.parse_event_config(blob(exportGuid=NEW_GUID)), "src2")
    except reg.GuidMismatch as err:
        reg.log_rejection(conn, err, "Register Event", "1000", "src2")
    [row] = rows(conn, "SELECT * FROM exception_log")
    assert row["category"] == "GUID validation" and row["operation"] == "Register Event"
    assert row["event_id"] == "1000" and row["source"] == "src2"
    assert row["resolution_status"] == "Open" and row["timestamp"].endswith("Z")
    assert GUID_1000 in row["message"] and NEW_GUID in row["message"]


def test_input_typos_are_not_written_to_the_exception_log(conn):
    reg.log_rejection(conn, reg.InputError("Enter the Event ID."), "Register Event", None, None)
    assert rows(conn, "SELECT * FROM exception_log") == []


def test_exception_rows_carry_only_safe_text(conn):
    err = _err(blob(eventName="x" * 500 + "<script>SECRET</script>"))
    reg.log_rejection(conn, err, "Register Event", "1000", "src")
    [row] = rows(conn, "SELECT message FROM exception_log")
    assert "SECRET" not in row["message"] and "<script>" not in row["message"]


def test_unknown_exception_category_is_refused():
    with pytest.raises(ValueError):
        exceptions.record(None, "Made up", "op", "msg")


def test_all_ten_brd_21_categories_exist():
    assert set(exceptions.CATEGORIES) == {
        "Download", "Azure Storage connectivity", "GUID validation", "Configuration", "File access",
        "Network/device connectivity", "Synchronisation", "Permission", "Missing folder",
        "Invalid configuration"}


def test_exception_log_write_failure_never_raises(cfg, caplog):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    c.close()
    with caplog.at_level("ERROR"):
        exceptions.record(c, exceptions.GUID_VALIDATION, "op", "msg")
    assert "Could not write exception_log" in caplog.text


# --- re-registration (owner decision: paste the new GUID from the web app) ---------------------

@pytest.fixture
def registered(conn):
    reg.register_event(conn, "1000", reg.parse_event_config(SAMPLE.read_bytes()), "old src")
    return conn


def test_reregister_replaces_the_registration_when_the_pasted_guid_matches(registered):
    new = reg.parse_event_config(blob(exportGuid=NEW_GUID, exportTimestamp="2026-09-21T09:30:00.000Z",
                                      eventName="Star contender Doha (final)"))
    reg.reregister_event(registered, "1000", new, "  {" + NEW_GUID.upper() + "}  ", "new src")
    [row] = rows(registered, "SELECT * FROM events")
    assert row["event_guid"] == NEW_GUID and row["event_name"] == "Star contender Doha (final)"
    assert row["last_updated"] == "2026-09-21T09:30:00Z" and row["status"] == "Registered"
    assert row["configuration_file"] == "new src"
    assert json.loads(row["configuration_json"])["exportGuid"] == NEW_GUID
    ops = [r["operation"] for r in rows(registered, "SELECT operation FROM operation_log ORDER BY log_id")]
    assert ops == ["Event Registered", "Event Re-registered"]


def test_reregister_audit_row_records_the_old_and_new_guid_and_the_source(registered):
    reg.reregister_event(registered, "1000", reg.parse_event_config(blob(exportGuid=NEW_GUID)), NEW_GUID, "new src")
    [msg] = [r["message"] for r in rows(registered, "SELECT message FROM operation_log WHERE operation = 'Event Re-registered'")]
    assert GUID_1000 in msg and NEW_GUID in msg and "new src" in msg and "->" in msg


def test_register_audit_row_records_the_guid_and_the_source(conn):
    reg.register_event(conn, "1000", reg.parse_event_config(SAMPLE.read_bytes()), "my src")
    [msg] = [r["message"] for r in rows(conn, "SELECT message FROM operation_log")]
    assert GUID_1000 in msg and "my src" in msg


def test_reregister_clears_download_and_sync_markers_because_nothing_came_from_the_new_export(registered):
    registered.execute("UPDATE events SET last_download='2026-09-19T10:00:00Z', last_sync='2026-09-19T10:05:00Z', "
                       "status='Synced' WHERE event_id='1000'")
    registered.commit()
    reg.reregister_event(registered, "1000", reg.parse_event_config(blob(exportGuid=NEW_GUID)), NEW_GUID, "s")
    [row] = rows(registered, "SELECT last_download, last_sync, status FROM events")
    assert (row["last_download"], row["last_sync"], row["status"]) == (None, None, "Registered")


def test_reregister_refuses_when_the_pasted_guid_is_not_the_files_guid(registered):
    new = reg.parse_event_config(blob(exportGuid=NEW_GUID))
    with pytest.raises(reg.RegistrationError) as exc:
        reg.reregister_event(registered, "1000", new, GUID_1000, "src")          # pasted the OLD guid
    assert exc.value.category == exceptions.GUID_VALIDATION and exc.value.log is True
    assert rows(registered, "SELECT event_guid FROM events")[0]["event_guid"] == GUID_1000


@pytest.mark.parametrize("pasted", ["", "garbage", "12345", NEW_GUID[:-1], NEW_GUID.replace("-", "")])
def test_reregister_refuses_a_malformed_paste_without_logging_it_as_an_exception(registered, pasted):
    new = reg.parse_event_config(blob(exportGuid=NEW_GUID))
    with pytest.raises(reg.InputError):
        reg.reregister_event(registered, "1000", new, pasted, "src")


def test_reregister_requires_the_event_to_exist(conn):
    new = reg.parse_event_config(blob(exportGuid=NEW_GUID))
    with pytest.raises(reg.RegistrationError, match="not registered"):
        reg.reregister_event(conn, "1000", new, NEW_GUID, "src")


def test_reregister_still_refuses_a_guid_owned_by_another_event(registered):
    reg.register_event(registered, "2000",
                       reg.parse_event_config(blob(eventId="2000", eventName="Two", exportGuid=NEW_GUID)), "s")
    stolen = reg.parse_event_config(blob(exportGuid=NEW_GUID))                    # event 1000 claims 2000's GUID
    with pytest.raises(reg.RegistrationError, match="different registered event"):
        reg.reregister_event(registered, "1000", stolen, NEW_GUID, "src")


def test_reregister_checks_the_event_id_matches_the_file(registered):
    new = reg.parse_event_config(blob(eventId="2000", exportGuid=NEW_GUID))
    with pytest.raises(reg.RegistrationError, match="1000"):
        reg.reregister_event(registered, "1000", new, NEW_GUID, "src")


def test_reregister_keeps_existing_mappings_for_now(registered):
    """Whether mappings should be kept or cleared is raised with the owner at Step 5.2."""
    registered.execute("INSERT INTO led_mappings (event_id, table_number, led_type, shared_folder) "
                       "VALUES ('1000', 1, 'Inner', 'X:\\\\share')")
    registered.commit()
    reg.reregister_event(registered, "1000", reg.parse_event_config(blob(exportGuid=NEW_GUID)), NEW_GUID, "s")
    assert len(rows(registered, "SELECT * FROM led_mappings")) == 1


# --- the pending store --------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _cfg():
    return reg.parse_event_config(SAMPLE.read_bytes())


def test_pending_entries_expire():
    clock = Clock()
    store = reg.PendingReregistrations(ttl_seconds=60, clock=clock)
    token = store.add("1000", _cfg(), "src", GUID_1000)
    assert store.get(token).event_id == "1000"
    clock.now += 61
    assert store.get(token) is None


def test_pending_is_bounded_and_drops_the_oldest():
    clock = Clock()
    store = reg.PendingReregistrations(max_items=3, clock=clock)
    tokens = []
    for _ in range(5):
        clock.now += 1
        tokens.append(store.add("1000", _cfg(), "src", None))
    assert [store.get(t) is not None for t in tokens] == [False, False, True, True, True]


def test_pending_discard_and_unknown_tokens():
    store = reg.PendingReregistrations()
    token = store.add("1000", _cfg(), "src", None)
    store.discard(token)
    assert store.get(token) is None
    for bad in (None, "", "nope", "x" * 1000):
        assert store.get(bad) is None
    store.discard(None)
    store.discard("nope")


def test_pending_tokens_are_unguessable_and_distinct():
    store = reg.PendingReregistrations(max_items=100)
    tokens = {store.add("1000", _cfg(), "src", None) for _ in range(50)}
    assert len(tokens) == 50 and all(len(t) >= 30 for t in tokens)


# --- the shipped test files behave exactly as their README says ---------------------------------------

FILES = ROOT / "docs" / "testfiles" / "phase3"


@pytest.mark.parametrize("name,entered,ok,fragment", [
    ("1000_valid.json", "1000", True, ""),
    ("2000_second_event.json", "2000", True, ""),
    ("1000_valid.json", "2000", False, "for event 1000"),
    ("bad_malformed.json", "3000", False, "valid JSON"),
    ("bad_missing_event_name.json", "3001", False, "eventName"),
    ("bad_no_tables.json", "3002", False, "at least one table"),
    ("bad_invalid_guid.json", "3003", False, "GUID"),
])
def test_shipped_test_files(conn, name, entered, ok, fragment):
    path = FILES / name
    try:
        cfg = reg.parse_event_config(path.read_bytes())
        reg.register_event(conn, entered, cfg, "test")
        assert ok
    except reg.RegistrationError as err:
        assert not ok and fragment in err.message


def test_shipped_duplicate_and_reexport_files(conn):
    reg.register_event(conn, "1000", reg.parse_event_config((FILES / "1000_valid.json").read_bytes()), "a")
    with pytest.raises(reg.RegistrationError, match="different registered event"):
        reg.register_event(conn, "2001",
                           reg.parse_event_config((FILES / "2001_duplicate_guid_of_1000.json").read_bytes()), "b")
    again = reg.parse_event_config((FILES / "1000_reexported_new_guid.json").read_bytes())
    with pytest.raises(reg.GuidMismatch):
        reg.register_event(conn, "1000", again, "c")
    reg.reregister_event(conn, "1000", again, NEW_GUID, "c")
    assert rows(conn, "SELECT event_guid FROM events")[0]["event_guid"] == NEW_GUID


def test_registration_module_never_touches_the_admin_credential_or_settings_table():
    """Security B2 scope guard: registration must not read application_settings."""
    text = (ROOT / "ledsync" / "services" / "registration.py").read_text(encoding="utf-8")
    assert "application_settings" not in text and "admin_" not in text


# --- architect review, Phase 3 --------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "a\ud800b",            # lone surrogate: used to crash registration with a 500 and no log row
    "a\udfffb",
    "line1\u2028line2",    # line separator
    "para\u2029graph",
    "nel\x85char",         # C1 control
    "del\x7fchar",
    "tab\tchar",
    "rlo\u202eXYZ",        # right-to-left override: spoofs how the name is displayed
    "lro\u202dXYZ",
    "isolate\u2066XYZ\u2069",
    "embed\u202bXYZ\u202c",
])
def test_names_with_surrogates_controls_and_bidi_spoofing_are_rejected_cleanly(name):
    for field in ("eventName", "eventStorageUrl"):
        override = {field: name if field == "eventName" else "https://h.example/" + name}
        err = _err(json.dumps({**REAL, **override}).encode("utf-8", errors="surrogatepass"))
        assert err.category == exceptions.INVALID_CONFIGURATION and "not allowed" in err.message


@pytest.mark.parametrize("name", [
    "\u062f\u0648\u062d\u0629 \u0627\u0644\u0628\u0637\u0648\u0644\u0629",     # Arabic
    "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645",                          # Persian with ZWNJ (needed)
    "Star contender Doha \u2014 Final",
    "\u4e16\u754c\u4e52\u4e53\u7403",                                              # Chinese
    "Caf\u00e9 Open \U0001f3d3",                                                      # accents + emoji
    "a\u200db",                                                                        # ZWJ
    "left\u200eright\u200f",                                                          # LRM / RLM
])
def test_legitimate_international_event_names_are_accepted(name):
    assert reg.parse_event_config(blob(eventName=name)).event_name == name


def test_a_surrogate_can_no_longer_reach_the_database_or_crash_registration(conn):
    with pytest.raises(reg.RegistrationError):
        reg.register_event(conn, "1000", reg.parse_event_config(
            json.dumps({**REAL, "eventName": "a\ud800b"}).encode("utf-8", errors="surrogatepass")), "src")
    assert rows(conn, "SELECT * FROM events") == []


@pytest.mark.parametrize("stamp,expected", [
    ("2026-09-18T07:02:26.380Z", "2026-09-18T07:02:26.380Z"),
    ("2026-09-18T10:02:26.380+03:00", "2026-09-18T07:02:26.380Z"),     # offset -> UTC
    ("2026-09-18 07:02:26", "2026-09-18T07:02:26Z"),                   # naive -> UTC, spelt uniformly
    ("2026-09-18T07:02:26.000Z", "2026-09-18T07:02:26Z"),
    ("2026-09-18", "2026-09-18T00:00:00Z"),
    ("1969-01-01T00:00:00Z", None), ("2100-01-01T00:00:00Z", None), ("whenever", None), (12345, None), (None, None),
])
def test_export_timestamp_is_normalised_to_utc_at_the_door(stamp, expected):
    assert reg.parse_event_config(blob(exportTimestamp=stamp)).export_timestamp == expected


def test_event_and_audit_row_are_saved_together_or_not_at_all(conn, monkeypatch):
    """If the audit row cannot be written, the registration must not exist either."""
    def boom(*a, **k):
        raise __import__("sqlite3").OperationalError("disk I/O error")

    monkeypatch.setattr(reg.oplog, "add", boom)
    with pytest.raises(reg.RegistrationError) as exc:
        reg.register_event(conn, "1000", reg.parse_event_config(SAMPLE.read_bytes()), "src")
    assert "could not be saved" in exc.value.message and exc.value.log is False
    assert rows(conn, "SELECT * FROM events") == []          # rolled back: no event without its audit row


def test_reregistration_is_also_all_or_nothing(registered, monkeypatch):
    def boom(*a, **k):
        raise __import__("sqlite3").OperationalError("disk I/O error")

    monkeypatch.setattr(reg.oplog, "add", boom)
    with pytest.raises(reg.RegistrationError):
        reg.reregister_event(registered, "1000", reg.parse_event_config(blob(exportGuid=NEW_GUID)), NEW_GUID, "s")
    assert rows(registered, "SELECT event_guid FROM events")[0]["event_guid"] == GUID_1000     # unchanged
