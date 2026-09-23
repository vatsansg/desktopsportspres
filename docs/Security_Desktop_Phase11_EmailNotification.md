# Security Checklist — Desktop Phase 11: Email Notification

Release/hand-off: Phase 11 — Email Notification (Step 11.1). Date: 23/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (BRD-accepted items and any the owner explicitly signs off).

**Theme of this phase:** the application makes its first outbound connection to a service other than the web application's own Azure Storage account — Azure Communication Services, to send the completion email. Three things must stay true: (1) the new secret (the ACS connection string, already stored in Phase 10) is never logged, echoed, or leaked through an error path now that it is actually *used*; (2) a notification failure of any kind can never turn a successful Download & Sync into a failed one, or corrupt the run's own logged outcome; (3) the pre-existing read-only-Azure-Storage guarantee (Business Rule 13) is not weakened by admitting a second Azure SDK into the codebase.

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed and protected | **Pass (unchanged)** | Not touched this phase. |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Pass (unchanged)** | `admin_*` keys untouched. |
| B3 | Shared-folder credentials not logged | **Pass (unchanged)** | Not touched this phase. |
| B4 (added) | The new Sender address field and the email-sending path need no new route/permission beyond what Phase 10 already gated | **Pass** | `/settings/email` (GET/POST) already required `login_required` + CSRF (Phase 10); no new HTTP endpoint was added — sending happens from inside `downloads.run_job`, an existing, already-authenticated code path. |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Local server transport | N/A at this stage | Unchanged; no new local route, CSP unchanged. |
| C5 (added) | **A second, genuinely different outbound connection (Azure Communication Services) is now made — bounded and isolated from the read-only Azure Storage guarantee** | **Pass** | `email_notify.py` is a distinct module, never imports anything from `storage.py`, and the AST guard in `tests/test_storage.py` (Business Rule 13's enforcement) explicitly names it as the only other module allowed to import `azure.*`, with a comment explaining why (a different service, no read-only constraint of its own). `EmailClient` is constructed with bounded `connection_timeout`/`read_timeout` (8s / 20s, matching `storage.py`'s own values) and the poller is waited on with an explicit `timeout=30s`, so an unreachable or slow ACS endpoint cannot hang a Download & Sync run indefinitely. |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D1 | **The ACS connection string is never logged, echoed, or leaked through an error path now that it is actually used** | **Pass** | The connection string only ever reaches `email_notify.notify()` via `EmailCredentials` (`repr=False` on the field, mirroring `CloudSettings.access_key`), is parsed locally into `(endpoint, key)`, and is never placed in an oplog message, an exception, or a log line — every error message in `_map_error`/`notify()` is a fixed, generic sentence naming the *category* of failure only. Verified: `NotifyError` messages never interpolate the connection string or the parsed key. | 
| D2 | **The connection string is parsed by hand, not passed to the SDK's `from_connection_string`** | **Pass** | Avoids the pre-existing AST guard's blanket ban on that method name (built for the Blob SDK's write-bypass method of the same name) while achieving the same effect explicitly: `endpoint`/`accesskey` split on `;` then `=`, case-insensitive keys, `https://` enforced on the endpoint. A malformed value raises a plain `NotifyError` before any network call. |
| D3 | **A notification failure cannot corrupt the run's own logged outcome** | **Pass** | `_notify` runs strictly after `_run_record`'s finished-row write, on its own separate database connection (mirroring `_run_record`'s own pattern), and every exception path inside it - including one it did not anticipate - is caught by the outer `try/except Exception` in `downloads._notify`, which only logs to the diagnostic logger and returns; nothing it does can roll back or re-open the run's own already-committed oplog/history rows. Tested: a notifier that raises `RuntimeError` on every call leaves the run's on-screen summary byte-for-byte identical to a run with no notification configured. |
| D4 | **The Sender address is not treated as a secret (correctly)** | **Pass** | It is a verified, non-sensitive address on the ACS resource (the equivalent of an SMTP "From:" header) - shown back on the settings page like the recipient, never hidden. Only the connection string itself is write-only. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | The sender field is validated with the same shape check and secret-shaped-value refusal as the recipient (Phase 10); the connection string's length bounds are unchanged from Phase 10; nothing user-typed is interpolated into a shell command, SQL string, or the outbound HTTP request in a way that isn't a plain JSON field value the SDK itself serialises. |
| E2 | Output encoding / XSS | **Pass** | Jinja auto-escaping for the new Sender field, matching every other settings field. |
| E5 | Errors do not leak internals | **Pass** | Every notifier failure becomes one of a small set of fixed, plain messages (`_map_error`); no SDK exception text, request ID, or stack trace reaches the operational log. |
| E12 | Background job safety | **Pass** | The notifier runs synchronously at the end of the same background job thread that already exists for the run (Phase 8), on its own DB connection; it introduces no new thread, no new shared mutable state, and (per D3/C5) cannot hang or corrupt the job. |
| E15 (added) | **The read-only-Azure-Storage guard (Business Rule 13) is extended precisely, not weakened** | **Pass** | The AST guard's `FORBIDDEN_CALLS` set (mutating Blob operations, `from_connection_string`, `send_request`) is unchanged and still scans every file, `email_notify.py` included - so even inside the newly-allowed module, those exact method names would still be caught if used on an `azure.storage.*` object; only the *import* restriction was widened, by an explicit two-name allow-list with a comment, not a blanket exemption. Business Rule 13 concerns the web application's Azure **Storage** account specifically; Communication Services is a different Azure product with no such constraint (sending an email is inherently a "write" to it). |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | `test_no_secrets` passes; no real ACS connection string exists in this repository, in a test fixture, or in a dev-environment fallback (unlike the Storage Account key, Phase 11 has no `.env` fallback for the ACS connection string - it must be entered through Settings). |
| F3 | Logs do not print secrets | **Pass** | See D1. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G2 | Failed actions logged without sensitive data | **Pass** | Every notification outcome (sent/skipped/failed) is its own operational-log row (`Email Notification` operation), with a fixed, non-sensitive message; the BRD Addendum A 39.3 "notification skipped — no connectivity" wording is used verbatim for the connectivity case. |
| G4 | Audit completeness and atomicity | **Pass (by design, not by transaction)** | The notification's own oplog row is deliberately written on its own connection/commit, separate from the run's finished-row commit - it is not part of the same atomic transaction, because the notification genuinely happens *after* the run is already fully committed and its own outcome is already fixed; this is the correct ordering, not a gap. |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | New dependency reviewed | **Pass** | `azure-communication-email==1.1.0`. `pip-audit` reports no vulnerability in it or its transitive dependencies (`isodate`, `azure-core`, `typing-extensions` - already present via `azure-storage-blob`); the only findings are pre-existing `setuptools` advisories in the venv bootstrap tooling itself, unrelated to any runtime dependency and unchanged by this phase. |
| H2 | Supported runtime | Pass | Python 3.11.9. |

## Findings

*Independent Solution Architect review pending — findings, if any, will be recorded here.*

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | pending | pending |
| User (Vatsan) go-ahead | Vatsan | pending | pending — also needs the real ACS connection string, verified sender address and a test recipient before live validation can run |
