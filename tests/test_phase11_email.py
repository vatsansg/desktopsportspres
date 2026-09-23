"""Phase 11 - the completion email (BRD Section 23), sent via Azure Communication Services
(Addendum A Section 39.3), following every full Download & Sync run."""

import pytest
from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError

from conftest import csrf_from, db_rows
from fakes import http_error
from ledsync.db import connect, init_db
from ledsync.services import email_notify, oplog
from ledsync.services import settings as cs
from test_phase8_sync import ev, map_devices, seed, sync_all, text_of  # noqa: F401


def tok(client, path="/"):
    return csrf_from(client, path)


# --- settings: the new Sender address field --------------------------------------------------------------------------

def test_sender_address_is_validated_and_required_together_with_the_recipient(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    with pytest.raises(cs.SettingsError):
        cs.save_email(conn, True, "it@example.com", "", "")           # recipient given, no sender
    with pytest.raises(cs.SettingsError):
        cs.save_email(conn, False, "", "not-an-email", "")
    changed = cs.save_email(conn, True, "it@example.com", "noreply@example.com",
                            "endpoint=https://x.communication.azure.com/;accesskey=" + "A" * 40)
    assert "sender" in changed
    assert cs.load_email(conn).sender == "noreply@example.com"
    conn.close()


# --- connection string parsing (never the SDK's own from_connection_string) -------------------------------------------

def test_connection_string_is_parsed_by_hand_order_and_case_insensitive():
    assert email_notify._parse_connection_string(
        "endpoint=https://x.communication.azure.com/;accesskey=AAAA") == ("https://x.communication.azure.com", "AAAA")
    assert email_notify._parse_connection_string(
        "AccessKey=BBBB;Endpoint=https://y.communication.azure.com") == ("https://y.communication.azure.com", "BBBB")
    for bad in ("garbage", "endpoint=https://x.communication.azure.com/", "accesskey=AAAA",
               "endpoint=http://insecure.example.com/;accesskey=AAAA"):
        with pytest.raises(email_notify.NotifyError):
            email_notify._parse_connection_string(bad)


# --- content: exactly the BRD Section 23 fields -------------------------------------------------------------------

def _outcome(**over):
    base = dict(event_id="1000", event_name="Star contender Doha", when="23/09/26 08:15:00",
               identified=10, downloaded=9, synchronised=8, failed=1, status="Successful with Exceptions",
               error_summary="Table 1 Inner: a.png failed")
    base.update(over)
    return email_notify.RunOutcome(**base)


def test_the_email_content_carries_every_brd_section_23_field():
    subject, body = email_notify._content(_outcome())
    for text in ("Star contender Doha", "1000", "23/09/26 08:15:00", "10", "9", "8", "1",
                "Successful with Exceptions", "Table 1 Inner: a.png failed"):
        assert text in subject + body


def test_no_error_summary_line_when_there_is_nothing_to_report():
    _, body = email_notify._content(_outcome(error_summary="", status="Successful"))
    assert "Error summary" not in body


# --- notify(): every outcome is logged, nothing is ever raised --------------------------------------------------------

class FakePoller:
    def __init__(self, result=None, error=None, done=True):
        self._result, self._error, self._done = result, error, done

    def result(self, timeout=None):
        if self._error:
            raise self._error
        return self._result

    def done(self):
        return self._done


class FakeEmailClient:
    def __init__(self, error=None, done=True):
        self.sent = []
        self.error = error
        self.done = done

    def begin_send(self, message):
        self.sent.append(message)
        return FakePoller(error=self.error, done=self.done)


def _configure(conn, *, enabled=True, recipient="it@example.com", sender="noreply@example.com",
              connection="endpoint=https://x.communication.azure.com/;accesskey=" + "A" * 40):
    cs.save_email(conn, enabled, recipient, sender, connection)


def test_disabled_sends_nothing_and_logs_nothing(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    calls = []
    email_notify.notify(conn, _outcome(), client_factory=lambda cs_: calls.append(cs_) or FakeEmailClient())
    assert calls == []
    assert db_rows(cfg, "SELECT * FROM operation_log WHERE operation = 'Email Notification'") == []
    conn.close()


def test_enabled_but_incomplete_settings_are_skipped_and_logged(cfg):
    """save_email() itself refuses to enable notifications without a recipient and sender (tested
    above) - this defends the same invariant one layer down, in case the row is ever incomplete
    for some other reason (a partial migration, a manual edit)."""
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    conn.execute("INSERT INTO application_settings (setting_name, setting_value) VALUES ('email_enabled', '1')")
    conn.commit()
    calls = []
    email_notify.notify(conn, _outcome(), client_factory=lambda cs_: calls.append(cs_) or FakeEmailClient())
    assert calls == []
    rows = db_rows(cfg, "SELECT status, message FROM operation_log WHERE operation = 'Email Notification'")
    assert rows == [{"status": "Skipped", "message": "Notification skipped — email settings are incomplete."}]
    conn.close()


def test_a_successful_send_is_logged_and_carries_the_right_fields(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    _configure(conn)
    fake = FakeEmailClient()
    email_notify.notify(conn, _outcome(), client_factory=lambda _cs: fake)
    assert len(fake.sent) == 1
    message = fake.sent[0]
    assert message["senderAddress"] == "noreply@example.com"
    assert message["recipients"]["to"] == [{"address": "it@example.com"}]
    assert "Star contender Doha" in message["content"]["plainText"]
    rows = db_rows(cfg, "SELECT status, message FROM operation_log WHERE operation = 'Email Notification'")
    assert rows == [{"status": "Success", "message": "Notification sent to it@example.com."}]
    conn.close()


@pytest.mark.parametrize("error,expected_status,expected_fragment", [
    (ServiceRequestError("offline"), "Skipped", "no connectivity"),
    (ConnectionError("offline"), "Skipped", "no connectivity"),
    (ClientAuthenticationError("bad key"), "Failed", "refused the connection string"),
])
def test_send_failures_are_classified_logged_and_never_raised(cfg, error, expected_status, expected_fragment):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    _configure(conn)
    fake = FakeEmailClient(error=error)
    email_notify.notify(conn, _outcome(), client_factory=lambda _cs: fake)   # must not raise
    rows = db_rows(cfg, "SELECT status, message FROM operation_log WHERE operation = 'Email Notification'")
    assert len(rows) == 1 and rows[0]["status"] == expected_status
    assert expected_fragment in rows[0]["message"]
    conn.close()


def test_an_unexpected_exception_is_swallowed_and_logged_as_failed(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    _configure(conn)

    def boom(_cs):
        raise RuntimeError("some bug")

    email_notify.notify(conn, _outcome(), client_factory=boom)              # must not raise
    rows = db_rows(cfg, "SELECT status FROM operation_log WHERE operation = 'Email Notification'")
    assert rows == [{"status": "Failed"}]
    conn.close()


def test_an_http_error_is_logged_with_its_status_code(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    _configure(conn)
    fake = FakeEmailClient(error=http_error(429))
    email_notify.notify(conn, _outcome(), client_factory=lambda _cs: fake)
    rows = db_rows(cfg, "SELECT status, message FROM operation_log WHERE operation = 'Email Notification'")
    assert rows[0]["status"] == "Failed" and "429" in rows[0]["message"]
    conn.close()


# --- wired into the real Download & Sync job (downloads.run_job) -------------------------------------------------------

def test_a_full_download_and_sync_run_sends_exactly_one_notification_with_the_real_counts(ev, cfg, tmp_path_factory, monkeypatch):
    seed(ev)
    map_devices(ev, cfg, tmp_path_factory)
    conn = connect(cfg.db_path)
    _configure(conn)
    conn.close()

    calls = []
    import ledsync.services.downloads as downloads
    monkeypatch.setattr(downloads.email_notify, "notify", lambda conn, outcome, **kw: calls.append(outcome))
    sync_all(ev)

    assert len(calls) == 1
    outcome = calls[0]
    assert outcome.event_id == "1000" and outcome.event_name == "Star contender Doha"
    assert (outcome.identified, outcome.downloaded, outcome.synchronised, outcome.failed) == (9, 5, 4, 0)  # 5 to
    # download + 4 to sync, accumulated across both phases - matches the oplog run-summary row exactly
    assert outcome.status == "Successful"


def test_a_download_only_run_sends_no_notification(ev, cfg, tmp_path_factory, monkeypatch):
    seed(ev)
    conn = connect(cfg.db_path)
    _configure(conn)
    conn.close()

    calls = []
    import ledsync.services.downloads as downloads
    monkeypatch.setattr(downloads.email_notify, "notify", lambda conn, outcome, **kw: calls.append(outcome))
    ev.post("/events/1000/changes/download", data={"csrf_token": tok(ev, "/events/1000")}, follow_redirects=True)

    assert calls == []


def test_brd_status_mapping_distinguishes_partial_failure_from_a_hard_stop():
    import ledsync.services.downloads as downloads
    assert downloads._brd_status("done", 0) == "Successful"
    assert downloads._brd_status("done", 3) == "Successful with Exceptions"
    assert downloads._brd_status("error", 0) == "Failed"
    assert downloads._brd_status("error", 5) == "Failed"
    assert downloads._brd_status("cancelled", 0) == "Cancelled"


def test_a_broken_notification_step_never_breaks_the_run_or_its_summary(ev, cfg, tmp_path_factory, monkeypatch):
    """`_notify` must be as safe as `_run_record`: a bug in it must not turn a successful run into an
    error page, and the run's own summary lines must be exactly as if no notification had been attempted."""
    seed(ev)
    map_devices(ev, cfg, tmp_path_factory)
    conn = connect(cfg.db_path)
    _configure(conn)
    conn.close()

    import ledsync.services.downloads as downloads

    def boom(conn, outcome, **kw):
        raise RuntimeError("a bug in the notifier")
    monkeypatch.setattr(downloads.email_notify, "notify", boom)
    out = text_of(sync_all(ev).get_data(as_text=True))
    assert "Downloaded 5 file(s)" in out and "Synchronised 4 file(s)" in out


# --- review fixes: a still-running poller must not be read as a confirmed send ------------------------------------------

def test_a_send_still_running_at_the_timeout_is_not_reported_as_sent(cfg):
    """Independent review finding: LROPoller.result(timeout=...) returns quietly, with no exception,
    if the operation has not yet reached a terminal state when the wait gives up - that must not be
    read as success, or the audit log would claim a send that may never complete or may still fail."""
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    _configure(conn)
    fake = FakeEmailClient(done=False)                     # result() returns cleanly, but not finished
    email_notify.notify(conn, _outcome(), client_factory=lambda _cs: fake)
    rows = db_rows(cfg, "SELECT status, message FROM operation_log WHERE operation = 'Email Notification'")
    assert rows[0]["status"] == "Failed" and "did not confirm" in rows[0]["message"]
    conn.close()


def test_a_confirmed_send_still_reports_success(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    _configure(conn)
    fake = FakeEmailClient(done=True)
    email_notify.notify(conn, _outcome(), client_factory=lambda _cs: fake)
    rows = db_rows(cfg, "SELECT status FROM operation_log WHERE operation = 'Email Notification'")
    assert rows == [{"status": "Success"}]
    conn.close()


# --- review fixes: enabling with no connection string ever configured is refused, not left broken forever --------------

def test_enabling_notifications_with_no_connection_string_ever_saved_is_refused(cfg):
    """Independent review finding: `enabled` previously only required a recipient and a sender, so the
    Settings UI could save enabled=1 with an empty connection string and stay silently non-functional
    (every run logging "Skipped - email settings are incomplete" forever) with no error shown at save
    time."""
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    with pytest.raises(cs.SettingsError, match="connection string"):
        cs.save_email(conn, True, "it@example.com", "noreply@example.com", "")
    assert not cs.load_email(conn).enabled
    conn.close()


def test_enabling_without_retyping_an_already_saved_connection_string_still_works(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    cs.save_email(conn, False, "it@example.com", "noreply@example.com",
                  "endpoint=https://x.communication.azure.com/;accesskey=" + "A" * 40)
    cs.save_email(conn, True, "it@example.com", "noreply@example.com", "")     # blank keeps the existing one
    assert cs.load_email(conn).enabled
    conn.close()


# --- review fix: email_notify.py's own azure imports stay narrower than "any azure.*" -----------------------------------

def test_email_notify_imports_only_communication_and_core_azure_modules():
    """tests/test_storage.py's shared AZURE_ALLOWED_MODULES list can only express "any azure.* import
    is fine in this file" - this asserts the narrower promise made in its comment: email_notify.py
    itself never imports outside azure.communication./azure.core."""
    import ast
    from pathlib import Path
    path = Path(email_notify.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            if name.startswith("azure"):
                assert name.startswith(("azure.communication.", "azure.core.")), name


def test_the_real_client_builds_offline_with_bounded_timeouts(monkeypatch):
    """Mirrors storage.py's own equivalent test (test_storage.py): the real, non-faked construction
    path must also be exercised, not just the injected fake used by every other test in this file."""
    key = "A" * 40
    client = email_notify._default_client(f"endpoint=https://example.communication.azure.com/;accesskey={key}")
    assert email_notify.CONNECT_TIMEOUT <= 10 and email_notify.READ_TIMEOUT <= 30  # an offline venue fails fast
    assert client is not None
