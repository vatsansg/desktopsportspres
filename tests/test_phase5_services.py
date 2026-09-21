"""Phase 5 services: LED structure, device/folder mapping rules, connection testing (Steps 5.1-5.3)."""

import json
import os
import threading
import time
import types
from pathlib import Path

import pytest

from ledsync.db import connect, init_db
from ledsync.services import connectivity, exceptions, mappings, structure
from ledsync.services import registration as reg

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "reference-docs" / "samplefiles-webassetmgmt" / "_GUID.json"
REAL = json.loads(SAMPLE.read_text(encoding="utf-8"))


@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    yield c
    c.close()


def register(conn, **overrides):
    obj = dict(REAL, **overrides)
    return reg.register_event(conn, str(obj["eventId"]), reg.parse_event_config(json.dumps(obj).encode()),
                              "https://x/_GUID.json")


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params)]


def load(conn, event_id="1000"):
    return structure.load_structure(conn, event_id)


def tables(*specs):
    return [{"tableNumber": n, "innerLed": i, "outerLed": o, "mainLed": m} for n, i, o, m in specs]


# --- structure (5.1) -------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Inner", "Inner"), ("inner", "Inner"), (" INNER ", "Inner"), ("Outer", "Outer"), ("outer", "Outer"),
    ("MainLED", "MainLED"), ("mainled", "MainLED"), ("Main LED", "MainLED"), ("main_led", "MainLED"),
    ("main-led", "MainLED"), ("Other", None), ("", None), (None, None), ("Inner2", None),
], ids=lambda v: repr(v)[:20])
def test_canonical_led_type(text, expected):
    assert structure.canonical_led_type(text) == expected


def test_the_real_event_structure_matches_the_export(conn):
    register(conn)
    s = load(conn)
    assert [(t.number, t.inner, t.outer, t.main) for t in s.tables] == [(1, True, True, True), (2, True, False, False)]
    assert s.enabled_pairs == ((1, "Inner"), (1, "Outer"), (1, "MainLED"), (2, "Inner"))


def test_structure_shows_exactly_what_the_file_says_including_a_table_with_nothing_enabled(conn):
    register(conn, tables=tables((3, False, False, False), (1, False, True, False)))
    s = load(conn)
    assert [t.number for t in s.tables] == [1, 3]
    assert s.enabled_pairs == ((1, "Outer"),)


def test_tables_are_sorted_numerically_not_as_text(conn):
    register(conn, tables=tables((10, True, False, False), (2, True, False, False), (1, True, False, False)))
    assert [t.number for t in load(conn).tables] == [1, 2, 10]


def test_structure_lookup_ignores_event_id_case(conn):
    register(conn, eventId="Doha-A")
    assert load(conn, "doha-a").tables


def test_structure_of_unknown_event_or_bad_stored_config_fails_safely(conn):
    with pytest.raises(structure.StructureError):
        load(conn, "nope")
    register(conn)
    conn.execute("UPDATE events SET configuration_json = ?", ("{not json",))
    with pytest.raises(structure.StructureError) as err:
        load(conn)
    assert "{not json" not in str(err.value)
    conn.execute("UPDATE events SET configuration_json = NULL")
    with pytest.raises(structure.StructureError):
        load(conn)


# --- mapping rows follow the structure --------------------------------------------------------------------

def test_registering_creates_one_unmapped_row_per_enabled_led(conn):
    register(conn)
    found = mappings.list_mappings(conn, "1000")
    assert [(m.table_number, m.led_type) for m in found] == [(1, "Inner"), (1, "Outer"), (1, "MainLED"), (2, "Inner")]
    assert all(m.status == mappings.STATUS_UNMAPPED and m.enabled for m in found)
    assert [m.label for m in found] == ["Table 1 Inner", "Table 1 Outer", "Table 1 Main LED", "Table 2 Inner"]


def test_ensure_rows_is_idempotent_and_repairs_a_missing_row(conn):
    register(conn)
    conn.execute("DELETE FROM led_mappings WHERE table_number = 2")
    conn.commit()
    s = load(conn)
    mappings.ensure_rows(conn, "1000", s)
    mappings.ensure_rows(conn, "1000", s)
    conn.commit()
    assert len(mappings.list_mappings(conn, "1000")) == 4


def test_reconcile_hides_removed_leds_and_keeps_their_data(conn):
    register(conn)
    s = load(conn)
    mappings.save_mappings(conn, "1000", s, {(2, "Inner"): ("", r"\\dev\two")})
    mappings.reconcile(conn, "1000", [(1, "Inner")])
    conn.commit()
    assert [(m.table_number, m.led_type) for m in mappings.list_mappings(conn, "1000")] == [(1, "Inner")]
    hidden = mappings.list_mappings(conn, "1000", enabled=False)
    assert {(m.table_number, m.led_type): m.shared_folder for m in hidden}[(2, "Inner")] == r"\\dev\two"


# --- validation -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,ok", [
    ("", True), ("  ", True), ("192.168.1.20", True), ("10.0.0.1", True), ("::1", True), ("fe80::1", True),
    ("256.1.1.1", False), ("1.2.3", False), ("host.local", False), ("192.168.1.20; drop", False),
    ("192.168.1.20/24", False), ("<script>", False), ("1.2.3.4 5.6.7.8", False), ("a" * 60, False),
], ids=lambda v: repr(v)[:24])
def test_ip_validation(text, ok):
    if ok:
        assert mappings.validate_ip(text, "T") == text.strip()
    else:
        with pytest.raises(mappings.MappingError):
            mappings.validate_ip(text, "T")


@pytest.mark.parametrize("text", [
    r"\\device01\Inner", r"\\device01\Inner\sub folder", r"\\192.168.1.20\LED", r"\\dev-1.local\a_b",
    "//device01/Inner", r"C:\LEDTest\Table1", r"d:\led\inner", r"\\device01\share$",
], ids=lambda v: v[:24])
def test_good_shared_folders(text):
    out = mappings.validate_shared_folder(text, "T")
    assert out and "/" not in out


@pytest.mark.parametrize("text", [
    r"relative\folder", "folder", r"..\x", r"\\dev\a\..\b", r"C:\a\..\b", r"\\?\C:\x", r"\\.\PhysicalDrive0",
    r"\\dev", r"\\dev\\", r"\\\dev\share", r"C:\\", "C:\\", "C:", r"C:folder", r"\\dev\sh*re", r"C:\a?b",
    r"C:\a|b", 'C:\\a"b', r"C:\a<b>", "C:\\a\x00b", "C:\\a\nb", r"C:\CON", r"C:\a\NUL.txt", r"C:\a.", r"C:\a \b",
    r"http://host/share", r"ftp://x/y", r"C:\Windows", r"C:\Windows\System32\x", r"C:\Program Files\x",
    r"C:\ProgramData\x", "C:\\" + "a" * 250, r"\\dev\a:b", r"\\de v\share", "\u202eC:\\x",
], ids=lambda v: repr(v)[:24])
def test_bad_shared_folders_are_rejected(text):
    with pytest.raises(mappings.MappingError):
        mappings.validate_shared_folder(text, "T")


def test_application_data_folder_is_protected(cfg):
    for inside in (cfg.data_dir, cfg.data_dir / "sub"):
        with pytest.raises(mappings.MappingError):
            mappings.validate_shared_folder(str(inside), "T", (cfg.data_dir,))
    assert mappings.validate_shared_folder(str(cfg.data_dir) + "-other", "T", (cfg.data_dir,))


def test_a_blank_folder_means_not_mapped():
    assert mappings.validate_shared_folder("   ", "T") == ""


def test_error_messages_name_the_destination_and_do_not_echo_raw_control_text():
    with pytest.raises(mappings.MappingError) as err:
        mappings.validate_shared_folder("C:\\a\x07b", "Table 1 Inner")
    assert "Table 1 Inner" in str(err.value) and "\x07" not in str(err.value)


# --- save rules ------------------------------------------------------------------------------------------

def test_save_persists_and_reports_changes(conn):
    register(conn)
    s = load(conn)
    changed = mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("10.0.0.5", r"\\d1\Inner")})
    assert changed == ["Table 1 Inner"]
    m = mappings.list_mappings(conn, "1000")[0]
    assert (m.ip_address, m.shared_folder, m.status) == ("10.0.0.5", r"\\d1\Inner", mappings.STATUS_UNTESTED)
    assert mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("10.0.0.5", r"\\d1\Inner")}) == []


def test_ip_is_optional(conn):
    register(conn)
    mappings.save_mappings(conn, "1000", load(conn), {(1, "Inner"): ("", r"\\d1\Inner")})
    assert mappings.list_mappings(conn, "1000")[0].ip_address == ""


def test_one_bad_row_saves_nothing(conn):
    register(conn)
    s = load(conn)
    with pytest.raises(mappings.MappingError) as err:
        mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d1\Inner"), (1, "Outer"): ("bad-ip", r"\\d1\Outer")})
    assert "Table 1 Outer" in str(err.value)
    assert all(m.shared_folder == "" for m in mappings.list_mappings(conn, "1000"))


def test_every_bad_row_is_reported_together(conn):
    register(conn)
    with pytest.raises(mappings.MappingError) as err:
        mappings.save_mappings(conn, "1000", load(conn), {(1, "Inner"): ("x", ""), (1, "Outer"): ("", "nope")})
    assert "Table 1 Inner" in str(err.value) and "Table 1 Outer" in str(err.value)


@pytest.mark.parametrize("second", [r"\\D1\INNER", r"\\d1\inner", r"\\d1\Inner"])
def test_the_same_folder_cannot_serve_two_destinations_case_insensitively(conn, second):
    register(conn)
    with pytest.raises(mappings.MappingError) as err:
        mappings.save_mappings(conn, "1000", load(conn), {(1, "Inner"): ("", r"\\d1\Inner"), (1, "Outer"): ("", second)})
    assert "already used" in str(err.value)


def test_duplicate_of_a_saved_destination_not_in_this_post_is_refused(conn):
    register(conn)
    s = load(conn)
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d1\Inner")})
    with pytest.raises(mappings.MappingError):
        mappings.save_mappings(conn, "1000", s, {(1, "Outer"): ("", r"\\D1\inner")})


def test_swapping_two_folders_is_allowed(conn):
    register(conn)
    s = load(conn)
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\a"), (1, "Outer"): ("", r"\\d\b")})
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\b"), (1, "Outer"): ("", r"\\d\a")})
    got = {m.led_type: m.shared_folder for m in mappings.list_mappings(conn, "1000")[:2]}
    assert got == {"Inner": r"\\d\b", "Outer": r"\\d\a"}


def test_the_same_folder_may_be_used_by_different_events(conn):
    register(conn)
    register(conn, eventId="2000", eventName="Other", exportGuid="7c9e6679-7425-40de-944b-e07fc1f90ae7")
    mappings.save_mappings(conn, "1000", load(conn, "1000"), {(1, "Inner"): ("", r"\\d\a")})
    mappings.save_mappings(conn, "2000", load(conn, "2000"), {(1, "Inner"): ("", r"\\d\a")})


def test_entries_for_leds_that_are_not_enabled_are_ignored(conn):
    register(conn)
    mappings.save_mappings(conn, "1000", load(conn), {(2, "Outer"): ("", r"\\d\x"), (9, "Inner"): ("", r"\\d\y"),
                                                       (1, "Bogus"): ("", r"\\d\z")})
    assert rows(conn, "SELECT COUNT(*) AS n FROM led_mappings WHERE shared_folder <> ''")[0]["n"] == 0
    assert rows(conn, "SELECT COUNT(*) AS n FROM led_mappings")[0]["n"] == 4


def test_changing_the_destination_resets_the_test_status_but_an_unchanged_save_keeps_it(conn):
    register(conn)
    s = load(conn)
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\a")})
    m = mappings.list_mappings(conn, "1000")[0]
    mappings.record_test(conn, m, True, "ok", None)
    assert mappings.list_mappings(conn, "1000")[0].status == mappings.STATUS_OK
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\a")})
    assert mappings.list_mappings(conn, "1000")[0].status == mappings.STATUS_OK
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\b")})
    assert mappings.list_mappings(conn, "1000")[0].status == mappings.STATUS_UNTESTED
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", "")})
    assert mappings.list_mappings(conn, "1000")[0].status == mappings.STATUS_UNMAPPED


def test_save_is_audited_without_logging_the_typed_path_or_ip_verbatim_in_exceptions(conn):
    register(conn)
    mappings.save_mappings(conn, "1000", load(conn), {(1, "Inner"): ("10.0.0.5", r"\\d1\Inner")})
    log = rows(conn, "SELECT * FROM operation_log WHERE operation = 'Mapping Saved'")
    assert len(log) == 1 and "Table 1 Inner" in log[0]["message"]
    assert rows(conn, "SELECT COUNT(*) AS n FROM exception_log")[0]["n"] == 0


# --- recording a test -------------------------------------------------------------------------------------

def test_failed_test_is_stored_logged_and_raises_an_open_exception(conn):
    register(conn)
    mappings.save_mappings(conn, "1000", load(conn), {(1, "Inner"): ("", r"\\d1\Inner")})
    m = mappings.list_mappings(conn, "1000")[0]
    mappings.record_test(conn, m, False, "The device could not be reached.", exceptions.NETWORK_DEVICE)
    got = mappings.list_mappings(conn, "1000")[0]
    assert got.status == mappings.STATUS_FAILED and got.last_connection_test
    ex = rows(conn, "SELECT * FROM exception_log")
    assert len(ex) == 1 and ex[0]["category"] == exceptions.NETWORK_DEVICE and ex[0]["resolution_status"] == exceptions.OPEN
    assert (ex[0]["table_number"], ex[0]["led_type"], ex[0]["destination"]) == (1, "Inner", r"\\d1\Inner")
    assert rows(conn, "SELECT status FROM operation_log WHERE operation = 'Device Test'")[0]["status"] == "Failed"


def test_successful_test_raises_no_exception(conn):
    register(conn)
    mappings.save_mappings(conn, "1000", load(conn), {(1, "Inner"): ("", r"\\d1\Inner")})
    mappings.record_test(conn, mappings.list_mappings(conn, "1000")[0], True, "ok", None)
    assert rows(conn, "SELECT COUNT(*) AS n FROM exception_log")[0]["n"] == 0
    assert mappings.list_mappings(conn, "1000")[0].status == mappings.STATUS_OK


# --- re-registration keeps what still applies --------------------------------------------------------------

def test_reregistering_with_a_changed_structure_keeps_matching_hides_removed_and_adds_new(conn):
    register(conn)
    s = load(conn)
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\a"), (2, "Inner"): ("", r"\\d\b")})
    for m in mappings.list_mappings(conn, "1000"):
        if m.shared_folder:
            mappings.record_test(conn, m, True, "ok", None)
    new_guid = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    new = dict(REAL, exportGuid=new_guid, tables=tables((1, True, False, False), (3, False, True, False)))
    reg.reregister_event(conn, "1000", reg.parse_event_config(json.dumps(new).encode()), new_guid, "https://x/_GUID.json")
    now = {(m.table_number, m.led_type): m for m in mappings.list_mappings(conn, "1000")}
    assert set(now) == {(1, "Inner"), (3, "Outer")}
    assert now[(1, "Inner")].shared_folder == r"\\d\a" and now[(1, "Inner")].status == mappings.STATUS_UNTESTED
    assert now[(3, "Outer")].status == mappings.STATUS_UNMAPPED
    assert {(m.table_number, m.led_type) for m in mappings.list_mappings(conn, "1000", enabled=False)} >= {(2, "Inner"), (1, "Outer"), (1, "MainLED")}


# --- connectivity (5.3) -----------------------------------------------------------------------------------

def test_a_writable_folder_passes_and_leaves_nothing_behind(tmp_path):
    (tmp_path / "keep.txt").write_text("x")
    result = connectivity.check_folder(str(tmp_path))
    assert result.ok and result.status_text == "Connection Successful" and not result.warning
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]


def test_an_empty_folder_passes(tmp_path):
    assert connectivity.check_folder(str(tmp_path)).ok


def test_missing_folder_is_reported_as_missing(tmp_path):
    result = connectivity.check_folder(str(tmp_path / "nope"))
    assert not result.ok and result.category == exceptions.MISSING_FOLDER and "does not exist" in result.message


def test_a_file_is_not_a_folder(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    result = connectivity.check_folder(str(f))
    assert not result.ok and "file, not a folder" in result.message


def test_the_probe_never_overwrites_and_has_a_recognisable_unique_name(monkeypatch, tmp_path):
    names = []
    real = connectivity._probe_name
    monkeypatch.setattr(connectivity, "_probe_name", lambda: names.append(real()) or names[-1])
    connectivity.check_folder(str(tmp_path))
    connectivity.check_folder(str(tmp_path))
    assert len(set(names)) == 2 and all(n.startswith(".ledsync-probe-") and n.endswith(".tmp") for n in names)

    taken = tmp_path / "taken.tmp"
    taken.write_text("precious")
    monkeypatch.setattr(connectivity, "_probe_name", lambda: "taken.tmp")
    result = connectivity.check_folder(str(tmp_path))
    assert not result.ok and taken.read_text() == "precious"      # exclusive create: refuses, never truncates


def test_a_probe_that_cannot_be_removed_is_a_warning_not_a_failure(monkeypatch, tmp_path):
    def refuse(path):
        raise PermissionError(5, "denied")
    patched_os(monkeypatch, remove=refuse)
    result = connectivity.check_folder(str(tmp_path))
    assert result.ok and "could not be removed" in result.warning and ".ledsync-probe-" in result.warning


def patched_os(monkeypatch, **overrides):
    """Give connectivity.py its own os with some calls replaced, without touching the real os module."""
    names = ("stat", "scandir", "remove", "path")
    fake = types.SimpleNamespace(**{n: getattr(os, n) for n in names})
    for name, fn in overrides.items():
        setattr(fake, name, fn)
    monkeypatch.setattr(connectivity, "os", fake)


def _oserror(cls, winerror=None):
    err = cls(22, "x")
    if winerror is not None:
        err.winerror = winerror
    return err


@pytest.mark.parametrize("err,category,fragment", [
    (_oserror(PermissionError), exceptions.PERMISSION, "Access was denied"),
    (_oserror(OSError, 5), exceptions.PERMISSION, "Access was denied"),
    (_oserror(OSError, 1326), exceptions.PERMISSION, "Access was denied"),
    (_oserror(OSError, 1219), exceptions.PERMISSION, "Access was denied"),
    (_oserror(OSError, 19), exceptions.PERMISSION, "read-only"),
    (_oserror(FileNotFoundError, 53), exceptions.NETWORK_DEVICE, "could not be reached"),   # reported as FileNotFound by Python
    (_oserror(OSError, 64), exceptions.NETWORK_DEVICE, "could not be reached"),
    (_oserror(ConnectionResetError), exceptions.NETWORK_DEVICE, "could not be reached"),
    (_oserror(TimeoutError), exceptions.NETWORK_DEVICE, "did not respond"),
    (_oserror(OSError, 67), exceptions.MISSING_FOLDER, "does not exist"),
    (_oserror(FileNotFoundError, 3), exceptions.MISSING_FOLDER, "does not exist"),
    (_oserror(OSError, 9999), exceptions.FILE_ACCESS, "could not be used"),
], ids=lambda v: v[:12] if isinstance(v, str) else f"{type(v).__name__}-{getattr(v, 'winerror', '')}")
def test_windows_errors_map_to_plain_language_and_brd_categories(monkeypatch, err, category, fragment):
    def boom(path):
        raise err
    patched_os(monkeypatch, stat=boom)
    result = connectivity.check_folder(r"\\somewhere\share")
    assert not result.ok and result.category == category and fragment in result.message
    assert "WinError" not in result.message and "Errno" not in result.message and "somewhere" not in result.message


def test_a_read_only_share_fails_on_the_write_step(monkeypatch, tmp_path):
    def refuse(path, mode="r", *a, **k):
        raise PermissionError(5, "denied")
    monkeypatch.setattr(connectivity, "open", refuse, raising=False)
    result = connectivity.check_folder(str(tmp_path))
    assert not result.ok and result.category == exceptions.PERMISSION and "writing" in result.message


def test_a_hanging_device_times_out_and_does_not_block_others(tmp_path):
    release = threading.Event()

    def check(path):
        if path == "hang":
            release.wait(5)
        return connectivity.CheckResult(True, "ok")

    start = time.monotonic()
    out = connectivity.check_many({"a": "hang", "b": "fine"}, timeout=0.3, _check=check)
    release.set()
    assert time.monotonic() - start < 2
    assert not out["a"].ok and out["a"].category == exceptions.NETWORK_DEVICE and "did not respond" in out["a"].message
    assert out["b"].ok


def test_many_dead_devices_share_one_deadline(tmp_path):
    release = threading.Event()
    start = time.monotonic()
    out = connectivity.check_many({i: "x" for i in range(8)}, timeout=0.4, _check=lambda p: release.wait(5))
    release.set()
    assert time.monotonic() - start < 1.5 and not any(r.ok for r in out.values())


def test_a_crashing_check_becomes_a_safe_failure_and_leaks_nothing():
    def crash(path):
        raise RuntimeError("secret internal detail C:\\x")
    result = connectivity.check_folder("anything", _check=crash)
    assert not result.ok and "secret" not in result.message and result.category == exceptions.FILE_ACCESS


def test_check_many_returns_a_result_for_every_key(tmp_path):
    good = tmp_path / "g"
    good.mkdir()
    out = connectivity.check_many({1: str(good), 2: str(tmp_path / "missing")})
    assert set(out) == {1, 2} and out[1].ok and not out[2].ok


def test_the_checker_never_touches_azure_or_the_network_stack():
    src = (ROOT / "ledsync" / "services" / "connectivity.py").read_text(encoding="utf-8")
    for banned in ("import socket", "subprocess", "azure", "requests", "urllib", "ping"):
        assert banned not in src.replace("ping is used at all", ""), banned
