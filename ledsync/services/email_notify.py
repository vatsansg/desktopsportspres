"""Email notification on Download & Sync completion (BRD Section 23), sent via Azure Communication
Services - the same service the web application uses (Desktop BRD Addendum A Section 39.3).

Sending is a SIDE CHANNEL: the run's own outcome (BRD Section 21/25) is already decided and recorded
before this module is ever called, and nothing here may change it. Every outcome of the send attempt -
sent, skipped (not configured/disabled, or Addendum A 39.3's new business rule for no internet
connectivity), or failed for some other reason - is written to the operational log (BRD Section 25) as
its own row, and never raised back to the caller.

Constructed by hand from the connection string's endpoint + access key (never the SDK's own
`from_connection_string`, which is one of the blanket-forbidden Azure SDK calls guarded by
tests/test_storage.py for a different reason - AST guards work on method names alone, so that guard
cannot tell this is an unrelated Communication Services client, not the read-only Blob Storage one).
"""

import logging
import sqlite3
from dataclasses import dataclass

from azure.communication.email import EmailClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError, ServiceResponseError

from . import oplog
from . import settings as cloud_settings

log = logging.getLogger("ledsync.email_notify")

OPERATION = "Email Notification"
SENT = "Success"
SKIPPED = "Skipped"
FAILED = "Failed"

CONNECT_TIMEOUT = 8     # seconds - an offline venue must not hang the run waiting on a notification
READ_TIMEOUT = 20
_POLL_TIMEOUT = 30      # seconds to wait for ACS to accept the send before giving up

class NotifyError(Exception):
    """Internal only - never raised past this module. `connectivity` marks the Addendum A 39.3 skip case."""

    def __init__(self, message: str, *, connectivity: bool = False):
        super().__init__(message)
        self.connectivity = connectivity


def _parse_connection_string(value: str) -> tuple[str, str]:
    """'endpoint=https://...;accesskey=...' (order and case insensitive, like every Azure connection
    string) -> (endpoint, key). Never the SDK's own parser/from_connection_string - see the module
    docstring for why this is done by hand."""
    parts = {}
    for piece in (value or "").split(";"):
        if "=" in piece:
            key, _, val = piece.partition("=")
            parts[key.strip().lower()] = val.strip()
    endpoint, key = parts.get("endpoint", "").rstrip("/"), parts.get("accesskey", "")
    if not (endpoint.startswith("https://") and key):
        raise NotifyError("The saved Azure Communication Services connection string is not in the expected form.")
    return endpoint, key


def _default_client(connection_string: str):
    endpoint, key = _parse_connection_string(connection_string)
    return EmailClient(endpoint, AzureKeyCredential(key),
                       connection_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT)


def _map_error(exc: Exception) -> NotifyError:
    if isinstance(exc, (ServiceRequestError, ServiceResponseError, TimeoutError, ConnectionError, OSError)):
        return NotifyError("Could not reach Azure Communication Services.", connectivity=True)
    if isinstance(exc, ClientAuthenticationError):
        return NotifyError("Azure Communication Services refused the connection string.")
    if isinstance(exc, HttpResponseError):
        return NotifyError(f"Azure Communication Services returned an error (status {getattr(exc, 'status_code', '?')}).")
    return NotifyError("An unexpected problem occurred sending the notification.")


@dataclass(frozen=True)
class RunOutcome:
    """Exactly the BRD Section 23 fields, gathered by the caller (downloads.run_job) from the run it
    just finished. `status` is one of the BRD Section 23 example wordings, or 'Cancelled' (a real
    outcome elsewhere in this application that the BRD's three examples do not cover)."""
    event_id: str
    event_name: str
    when: str               # already formatted DD/MM/YY HH:MM:SS local (BRD Section 25's log format)
    identified: int
    downloaded: int
    synchronised: int
    failed: int
    status: str              # Successful | Successful with Exceptions | Failed | Cancelled
    error_summary: str       # "" when there is nothing to report


def _content(outcome: RunOutcome) -> tuple[str, str]:
    subject = f"[LED Asset Sync] {outcome.event_name} ({outcome.event_id}) - {outcome.status}"
    body = (
        f"Event name: {outcome.event_name}\n"
        f"Event ID: {outcome.event_id}\n"
        f"Operation date/time: {outcome.when}\n"
        f"Files identified: {outcome.identified}\n"
        f"Files downloaded: {outcome.downloaded}\n"
        f"Files synchronised: {outcome.synchronised}\n"
        f"Files failed: {outcome.failed}\n"
        f"Overall status: {outcome.status}\n"
    )
    if outcome.error_summary:
        body += f"\nError summary:\n{outcome.error_summary}\n"
    return subject, body


def _send(client, sender: str, recipient: str, outcome: RunOutcome) -> None:
    subject, body = _content(outcome)
    message = {
        "senderAddress": sender,
        "recipients": {"to": [{"address": recipient}]},
        "content": {"subject": subject, "plainText": body},
    }
    try:
        poller = client.begin_send(message)
        poller.result(timeout=_POLL_TIMEOUT)
    except Exception as exc:                                   # noqa: BLE001 - mapped below
        raise _map_error(exc) from None
    # A terminal Failed/Cancelled status raises inside result() above (caught and mapped, not reached
    # here); a still-Running operation at the _POLL_TIMEOUT mark instead returns quietly with no
    # exception (LROPoller.wait() simply stops waiting), which must NOT be read as a confirmed send -
    # that would log "Notification sent" for a send that may never complete or may still fail.
    if not poller.done():
        raise NotifyError("Azure Communication Services did not confirm the send in time.")


def notify(conn: sqlite3.Connection, outcome: RunOutcome, *, client_factory=None) -> None:
    """Send the Section 23 completion email if notifications are configured and enabled. Best-effort:
    every outcome - sent, skipped, or failed - is logged and this function never raises."""
    creds = cloud_settings.load_email_secret(conn)
    if not creds.enabled:
        return                                                 # not asked for - nothing to log either
    if not (creds.recipient and creds.sender and creds.connection_string):
        oplog.record(conn, OPERATION, SKIPPED,
                     "Notification skipped — email settings are incomplete.", outcome.event_id)
        return
    factory = client_factory or _default_client
    try:
        client = factory(creds.connection_string)
        _send(client, creds.sender, creds.recipient, outcome)
    except NotifyError as err:
        if err.connectivity:
            oplog.record(conn, OPERATION, SKIPPED, "Notification skipped — no connectivity.", outcome.event_id)
        else:
            oplog.record(conn, OPERATION, FAILED, f"Notification failed: {err}", outcome.event_id)
        return
    except Exception:                                          # noqa: BLE001 - a notification must never break the run
        log.exception("Unexpected error sending the completion email")
        oplog.record(conn, OPERATION, FAILED, "Notification failed: an unexpected problem occurred.", outcome.event_id)
        return
    oplog.record(conn, OPERATION, SENT, f"Notification sent to {creds.recipient}.", outcome.event_id)
