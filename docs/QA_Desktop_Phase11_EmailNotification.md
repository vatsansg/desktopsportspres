# QA Test Case Document — Desktop Phase 11: Email Notification

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 11 — Email Notification (Step 11.1) |
| BRD reference(s) | Desktop BRD Section 23 (Email Notification), Section 14 (Email Settings), Section 31 (Scheduled Workflow); Desktop BRD Addendum A Section 39.3 (ACS mechanism, no-connectivity business rule) |
| Implementation Sequence reference | Step 11.1 |
| Date | 23/09/26 |
| Tested by | Claude Code (automated only — no live Azure Communication Services resource exists yet for this project; see "Live validation" below) |
| Environment | Windows 11 Pro, Python 3.11.9; a fake ACS client (dependency-injected) for the automated tests. |

## Owner decisions applied / defaults chosen (23/09/26) — my defaults, tell me if any is wrong

- **A completion email follows every full Download & Sync run** (both the download and the push steps, i.e. the Dashboard's "Download & Sync" action) when Settings → Email is enabled. It does **not** follow a Download-only or Sync-only partial run (the Change Log page's separate actions), and does not yet follow a *scheduled* run — Phase 12 (scheduling execution) does not exist yet, but BRD Section 31's own scheduled workflow already ends with "Send IT Email" after the full flow, so the same hook will cover it once Phase 12 arrives without further changes.
- **Sender address added to Settings → Email** (BRD Section 14, post-Phase-10's ACS deviation, didn't carry one forward explicitly, but Azure Communication Services requires a verified "from" address on every send — there is no way to send without one). Required together with the recipient when notifications are enabled.
- **BRD Section 23's overall status wording is extended with "Cancelled"** — a real outcome elsewhere in this application (a Download & Sync stopped mid-run) that the BRD's three examples (Successful / Successful with Exceptions / Failed) do not cover. The run-level operational log row's own Success/Failed/Cancelled/Started status vocabulary (Phase 9, unchanged) does not distinguish a run that finished with some file-level failures from one that stopped outright, but the notification content does, exactly per BRD 23's intent.
- **A notification failure never affects the run's own outcome** — the Download & Sync (or Download/Sync-only) operation is already complete and logged before the notification is even attempted; sending is a pure side channel. Every outcome — sent, skipped (not configured, or the Addendum A 39.3 no-connectivity rule), or failed for some other reason — is its own row in the operational log (`Email Notification` operation, `Success` / `Skipped` / `Failed` status).
- **The ACS connection string is parsed by hand**, not via the SDK's own `from_connection_string` (a pre-existing AST guard in `tests/test_storage.py`, built for a different reason — the read-only-Azure-Storage guarantee — blanket-forbids that method name everywhere by name alone; extended, not weakened, to explicitly allow `email_notify.py` to import the unrelated `azure.communication.email` service).

## What was built

- `services/email_notify.py`: builds the BRD Section 23 email content (event name, event ID, operation date/time, identified/downloaded/synchronised/failed counts, error summary, overall status), sends it via Azure Communication Services, classifies every send failure (no connectivity vs. an auth/HTTP/unexpected error), and logs the outcome. Never raises to its caller.
- `services/downloads.py`: `run_job` calls the notifier once, after the run-level "finished" oplog row is written, only for a full Download & Sync (`download and push`), on its own short database connection.
- `services/settings.py`: a new **Sender address** field (`email_sender`), validated the same way as the recipient; `save_email` now requires both a recipient and a sender when notifications are enabled. A new internal-only accessor (`load_email_secret` / `EmailCredentials`) carries the real connection string to the notifier — the page-facing `load_email` / `EmailSettings` view is unchanged (still never returns the connection string).
- `services/oplog.py`: `"Email Notification"` added to the fixed operation vocabulary (BRD Section 25); `"Skipped"` added to the status filter list so the Logs page can filter to it.
- `web/templates/settings_email.html` / `views_settings.py`: the Sender address field; the page's lede text updated now that Phase 11 actually sends (Phase 10's page said it did not yet).
- `requirements.txt`: `azure-communication-email==1.1.0` (pip-audit clean; no new vulnerable transitive dependency).

## Live validation against a real Azure Communication Services resource — NOT YET DONE

Unlike Phase 4 (where the owner supplied the real Azure Storage Account access key for live validation against Event 1000), **no real Azure Communication Services connection string or verified sender address exists yet for this project.** Addendum A Section 39.3 says these "will be supplied when Phase 11 begins" — Phase 11 has now begun. Everything below is proven against a dependency-injected fake ACS client; nothing has been sent to a real inbox. **Before this phase can be marked complete, the owner needs to supply:**
1. A real Azure Communication Services connection string.
2. A verified sender address/domain on that ACS resource.
3. A real recipient address to receive the test emails.

Once supplied, a live check (real Download & Sync against Event 1000, real email received) should be run and recorded here, the same way Phase 4's live Azure Storage validation was recorded.

## Test cases — automated

| ID | Description | Status |
|---|---|---|
| TC-A01 | **Sender address setting:** validated the same way as the recipient (shape check, key-shaped values refused); required together with the recipient when notifications are enabled; `OWNED_KEYS` covers the new key | Pass |
| TC-A02 | **Connection string parsing:** `endpoint=`/`accesskey=` parsed correctly regardless of order or case; a malformed string (missing a part, `http://` instead of `https://`) is refused with a plain message, never passed to the SDK | Pass |
| TC-A03 | **Email content:** every BRD Section 23 field appears in the subject/body with the real values; the error-summary line is omitted entirely when there is nothing to report | Pass |
| TC-A04 | **Disabled notifications:** the client factory is never even called, and nothing is logged — a silent no-op, not a "skipped" row (nothing was ever asked for) | Pass |
| TC-A05 | **Enabled but incomplete settings** (a state `save_email`'s own validation cannot normally produce, defended anyway): skipped and logged as `Skipped — email settings are incomplete`, client never called | Pass |
| TC-A06 | **A successful send:** the fake ACS client receives the correct sender/recipient/subject/body; the oplog row is `Success` naming the recipient | Pass |
| TC-A07 | **Send failures classified correctly and never raised:** `ServiceRequestError`/`ConnectionError` (no connectivity) → `Skipped — no connectivity` (Addendum A 39.3); `ClientAuthenticationError` (bad connection string) → `Failed`, a distinct message; an HTTP error → `Failed`, with the status code in the message; a completely unexpected exception (a bug) → caught, logged `Failed`, never propagates | Pass |
| TC-A08 | **Wired into a real Download & Sync run:** exactly one notification attempt, carrying the run's real identified/downloaded/synchronised/failed counts and the correct BRD status wording | Pass |
| TC-A09 | **Scoping:** a Download-only run (the Change Log page's separate action) sends no notification at all | Pass |
| TC-A10 | **BRD status wording:** done+0 errors → Successful; done+some errors → Successful with Exceptions; state=error → Failed; state=cancelled → Cancelled | Pass |
| TC-A11 | **A broken notifier never breaks the run:** the job's own on-screen summary ("Downloaded N file(s)…", "Synchronised N file(s)…") is unchanged even when the notification step itself raises | Pass |
| TC-A12 | **Read-only-Azure-Storage AST guard (`tests/test_storage.py`), re-verified:** still passes with the scope explicitly widened to `email_notify.py` for the unrelated Communication Services import; storage.py itself is untouched | Pass |

`.\.venv\Scripts\python -m pytest -q` → **1459 passed, 14 skipped** (the skipped are the live-Azure-**Storage** tests from earlier phases, which need `--live`; there is no live-ACS test yet — see "Live validation" above).

## Rendered-page check

| ID | Description | Result |
|---|---|---|
| TC-R01 | Settings → Email: updated lede text (notifications now actually send), the new Sender address field between the recipient and the connection string, validation message when enabling without a sender. One orange primary action; bordered inputs; consistent with the rest of Settings | Pass |

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-72 | No live validation against a real Azure Communication Services resource yet — see "Live validation" above. | Info | **Needs the owner's real ACS connection details before go-ahead** |
| F-73 | The notification does not yet fire after a *scheduled* run, because scheduled execution (Phase 12) does not exist yet. The hook point (`run_job`) is shared, so no further change should be needed once Phase 12 arrives. | Info | Carried to Phase 12 |
| F-74 | A Download-only or Sync-only partial run (the Change Log page's separate actions) never sends a notification, by design — only a full Download & Sync does. | Info | Accepted, tell me if wrong |

## Independent Solution Architect review

*Pending — see the Security Checklist for the same entry once complete.*

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | pending | pending |
| User (Vatsan) go-ahead | Vatsan | pending | pending — also needs the real ACS connection string, verified sender address and a test recipient (see "Live validation") |
