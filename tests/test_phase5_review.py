"""Phase 5 - regression tests for the independent architect review findings (21/09/26)."""

import ctypes
import json
import subprocess
from pathlib import Path

import pytest
from conftest import csrf_from, db_rows, serve_event
from test_phase5_services import _oserror, load, patched_os, register as register_service, rows

from ledsync.db import connect, init_db
from ledsync.services import connectivity, exceptions, mappings, structure

ROOT = Path(__file__).resolve().parent.parent
REAL = json.loads((ROOT / "reference-docs" / "samplefiles-webassetmgmt" / "_GUID.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    yield c
    c.close()


def register(conn):
    return register_service(conn)


def short_name(path) -> str:
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 512)
    return buf.value


# --- finding 1: protected folders reached by another name --------------------------------------------------------

@pytest.mark.parametrize("text", [
    r"C:\PROGRA~1\x", r"C:\PROGRA~2\x", r"C:\PROGRA~1", r"\\localhost\C$\Windows\Temp", r"\\LOCALHOST\share",
    r"\\127.0.0.1\C$\Windows", r"\\127.1.2.3\share", r"\\localhost.localdomain\share",
], ids=lambda v: v[:22])
def test_short_names_and_loopback_shares_cannot_reach_protected_folders(text):
    with pytest.raises(mappings.MappingError):
        mappings.validate_shared_folder(text, "T")


def test_this_computers_own_name_is_refused_as_a_network_host(monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "VENUE-PC")
    for host in (r"\\venue-pc\share", r"\\VENUE-PC.corp.local\share"):
        with pytest.raises(mappings.MappingError) as err:
            mappings.validate_shared_folder(host, "T")
        assert "this computer" in str(err.value)
    assert mappings.validate_shared_folder(r"\\venue-pc2\share", "T")


def test_the_data_folder_is_protected_under_its_short_and_long_names(cfg):
    short = short_name(cfg.data_dir)
    if not short or short.casefold() == str(cfg.data_dir).casefold():
        pytest.skip("this machine has no 8.3 short names for the test folder")
    for form in {str(cfg.data_dir), short}:
        with pytest.raises(mappings.MappingError):
            mappings.validate_shared_folder(form + r"\led", "T", (cfg.data_dir,))


def test_a_junction_into_a_protected_folder_is_refused_at_save_and_at_test(tmp_path_factory, cfg):
    root = tmp_path_factory.mktemp("jn")
    target = cfg.data_dir / "inside"
    target.mkdir()
    link = root / "link"
    out = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("could not create a junction on this machine")
    with pytest.raises(mappings.MappingError):
        mappings.validate_shared_folder(str(link), "T", (cfg.data_dir,))
    result = connectivity.check_folder(str(link), forbidden_roots=(cfg.data_dir,))     # e.g. the junction was added later
    assert not result.ok and result.category == exceptions.CONFIGURATION and "reserved" in result.message
    assert list(target.iterdir()) == []                                                  # nothing was written through it


def test_the_application_data_folder_is_also_refused_at_test_time(cfg):
    result = connectivity.check_folder(str(cfg.data_dir), forbidden_roots=(cfg.data_dir,))
    assert not result.ok and "reserved" in result.message


def test_two_names_for_the_same_local_folder_count_as_a_duplicate(conn, tmp_path_factory):
    real = tmp_path_factory.mktemp("dupe") / "Some Long Folder Name"
    real.mkdir()
    register(conn)
    with pytest.raises(mappings.MappingError) as err:
        mappings.save_mappings(conn, "1000", load(conn), {(1, "Inner"): ("", str(real)), (1, "Outer"): ("", short_name(real))})
    assert "already used" in str(err.value)


# --- finding 2: a returning hidden mapping ------------------------------------------------------------------------

def test_a_returning_hidden_mapping_cannot_duplicate_an_enabled_folder(conn):
    register(conn)
    s = load(conn)
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\X"), (2, "Inner"): ("", r"\\d\Y")})
    mappings.reconcile(conn, "1000", [(2, "Inner")])                       # T1 Inner hidden
    only_t2 = structure.EventStructure((structure.TableStructure(2, True, False, False),))
    mappings.save_mappings(conn, "1000", only_t2, {(2, "Inner"): ("", r"\\D\x")})    # T2 Inner takes T1 Inner's old folder
    mappings.reconcile(conn, "1000", [(1, "Inner"), (2, "Inner")])         # T1 Inner comes back
    conn.commit()
    enabled = {(m.table_number, m.led_type): m.shared_folder for m in mappings.list_mappings(conn, "1000")}
    assert enabled[(2, "Inner")] == r"\\D\x" and enabled[(1, "Inner")] == ""
    folders = [m.shared_folder.casefold() for m in mappings.list_mappings(conn, "1000") if m.shared_folder]
    assert len(folders) == len(set(folders))


def test_a_returning_hidden_mapping_keeps_its_folder_when_nothing_conflicts(conn):
    register(conn)
    mappings.save_mappings(conn, "1000", load(conn), {(1, "Inner"): ("", r"\\d\X")})
    mappings.reconcile(conn, "1000", [(2, "Inner")])
    mappings.reconcile(conn, "1000", [(1, "Inner"), (2, "Inner")])
    assert mappings.list_mappings(conn, "1000")[0].shared_folder == r"\\d\X"


# --- finding 3: stale results --------------------------------------------------------------------------------------

def test_a_stale_test_result_is_not_written_onto_a_changed_folder(conn):
    register(conn)
    s = load(conn)
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\old")})
    tested = mappings.list_mappings(conn, "1000")[0]                       # the row as it was when the test started
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\new")})     # operator changes it during the test
    assert mappings.record_test(conn, tested, True, "ok", None) is False
    now = mappings.list_mappings(conn, "1000")[0]
    assert now.shared_folder == r"\\d\new" and now.status == mappings.STATUS_UNTESTED
    assert rows(conn, "SELECT COUNT(*) AS n FROM operation_log WHERE operation = 'Device Test'")[0]["n"] == 0
    assert rows(conn, "SELECT COUNT(*) AS n FROM exception_log")[0]["n"] == 0


def test_a_stale_failure_does_not_raise_an_exception_row_either(conn):
    register(conn)
    mappings.save_mappings(conn, "1000", load(conn), {(1, "Inner"): ("", r"\\d\old")})
    tested = mappings.list_mappings(conn, "1000")[0]
    mappings.reconcile(conn, "1000", [(2, "Inner")])                        # re-registration hides it meanwhile
    assert mappings.record_test(conn, tested, False, "bad", exceptions.NETWORK_DEVICE) is False
    assert rows(conn, "SELECT COUNT(*) AS n FROM exception_log")[0]["n"] == 0


# --- notes ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "C:\\LED\\a\u202eexe.txt", "C:\\LED\\a\u200bb", "C:\\LED\\a\u3000b", "C:\\LED\\a\u00a0b", "C:\\LED\\a\ufeffb",
    "C:\\LED\\a\u2028b", "C:\\LED\\CON .txt", "C:\\LED\\COM\u00b9", "C:\\LED\\lpt\u00b3.log",
], ids=lambda v: ascii(v)[-14:])
def test_invisible_lookalike_and_reserved_names_in_the_middle_of_a_path_are_refused(text):
    with pytest.raises(mappings.MappingError):
        mappings.validate_shared_folder(text, "T")


def test_non_latin_folder_names_are_still_allowed():
    assert mappings.validate_shared_folder("\\\\device\\\u0645\u0644\u0639\u0628\\Table 1", "T")


def test_long_paths_are_refused_before_the_probe_name_could_overflow_windows_limits():
    with pytest.raises(mappings.MappingError):
        mappings.validate_shared_folder("C:\\" + "a" * (mappings.MAX_PATH_LENGTH - 2), "T")
    assert mappings.MAX_PATH_LENGTH + 1 + len(connectivity._probe_name()) < 260


def test_windows_error_206_says_the_path_is_too_long_not_that_it_is_missing(monkeypatch):
    def boom(path):
        raise _oserror(OSError, 206)
    patched_os(monkeypatch, stat=boom)
    result = connectivity.check_folder(r"\\d\s")
    assert result.category == exceptions.CONFIGURATION and "too long" in result.message


def test_an_unchanged_save_writes_no_audit_row(conn):
    register(conn)
    s = load(conn)
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\a")})
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\a")})
    assert rows(conn, "SELECT COUNT(*) AS n FROM operation_log WHERE operation = 'Mapping Saved'")[0]["n"] == 1


def test_a_probe_that_could_not_be_removed_is_audited(conn):
    register(conn)
    mappings.save_mappings(conn, "1000", load(conn), {(1, "Inner"): ("", r"\\d\a")})
    m = mappings.list_mappings(conn, "1000")[0]
    mappings.record_test(conn, m, True, "ok", None, warning="A small empty test file could not be removed (x).")
    log = rows(conn, "SELECT message FROM operation_log WHERE operation = 'Device Test'")
    assert "could not be removed" in log[0]["message"]


def test_the_partial_post_duplicate_message_names_the_right_destination(conn):
    register(conn)
    s = load(conn)
    mappings.save_mappings(conn, "1000", s, {(1, "Inner"): ("", r"\\d\a")})
    with pytest.raises(mappings.MappingError) as err:
        mappings.save_mappings(conn, "1000", s, {(1, "Outer"): ("", r"\\d\A")})
    assert str(err.value).startswith("Table 1 Outer:") and "used by Table 1 Inner" in str(err.value)


# --- the same findings through the web pages ---------------------------------------------------------------------------

@pytest.fixture
def ev(logged_in):
    obj = dict(REAL)
    serve_event(logged_in.application.extensions["test.azure"], obj)
    logged_in.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(logged_in, "/events/new")})
    return logged_in


@pytest.fixture
def shares(tmp_path_factory):
    return tmp_path_factory.mktemp("shares")


def post(client, data):
    return client.post("/events/1000/mappings", data={"csrf_token": csrf_from(client, "/events/1000"), **data},
                       follow_redirects=True)


def mapped(cfg):
    return db_rows(cfg, "SELECT * FROM led_mappings WHERE table_number = 1 AND led_type = 'Inner'")[0]


def test_signed_out_post_with_a_valid_csrf_token_goes_to_login_not_to_the_mapping_code(launched):
    token = csrf_from(launched, "/login")
    resp = launched.post("/events/1000/mappings", data={"csrf_token": token, "action": "save"})
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_a_database_failure_while_recording_a_test_is_a_plain_message_not_a_500(ev, shares, monkeypatch):
    def broken(*a, **k):
        raise mappings.MappingError("The test result could not be saved.")
    monkeypatch.setattr(mappings, "record_test", broken)
    good = shares / "a"
    good.mkdir()
    resp = post(ev, {"action": "test:1-Inner", "folder-1-Inner": str(good)})
    assert resp.status_code == 200 and "could not be saved" in resp.get_data(as_text=True)


def test_the_page_still_opens_if_the_mapping_rows_cannot_be_created(ev, monkeypatch):
    import sqlite3

    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(mappings, "ensure_rows", broken)
    assert ev.get("/events/1000").status_code == 200


def test_a_folder_changed_while_it_was_being_tested_is_not_marked_tested(ev, cfg, shares):
    old, new = shares / "old", shares / "new"
    old.mkdir()
    new.mkdir()
    post(ev, {"action": "save", "folder-1-Inner": str(old)})
    real = connectivity.check_many

    def checker_that_races(paths, **kw):
        c = connect(cfg.db_path)            # the operator saves another folder for that row while the test runs
        c.execute("UPDATE led_mappings SET shared_folder = ?, connection_status = NULL "
                  "WHERE table_number = 1 AND led_type = 'Inner'", (str(new),))
        c.commit()
        c.close()
        return real(paths, **kw)
    ev.application.extensions["ledsync.checker"] = checker_that_races
    html = post(ev, {"action": "test:1-Inner", "folder-1-Inner": str(old)}).get_data(as_text=True)
    assert "changed while it was being tested" in html
    assert mapped(cfg)["shared_folder"] == str(new) and mapped(cfg)["connection_status"] is None


def test_the_first_cell_of_each_mapping_row_is_a_row_header(ev):
    assert ev.get("/events/1000").get_data(as_text=True).count('<th scope="row" class="nowrap">Table') == 4


def test_a_loopback_share_is_refused_through_the_page(ev, cfg):
    resp = post(ev, {"action": "save", "folder-1-Inner": r"\\localhost\C$\Windows\Temp"})
    assert "this computer" in resp.get_data(as_text=True)
    assert mapped(cfg)["shared_folder"] == ""
