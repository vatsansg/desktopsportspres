# Security Checklist — Desktop Phase 10: Remaining Settings and Dashboard Polish

Release/hand-off: Phase 10 — Remaining Application Settings and Dashboard Polish (Step 10.1, Step 10.2). Date: 22/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (BRD-accepted items and any the owner explicitly signs off).

**Theme of this phase:** the application gains four new Settings screens (one holding a second secret, the future email connection string), a per-event schema change, and a shared, mutable retry configuration used by every background job. Three things must stay true: (1) the new secret is handled exactly as carefully as the existing storage key — never echoed, never logged; (2) the schema change never loses data on an upgrade; (3) the mutable retry setting can never leak from one run into an unrelated one.

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed and protected | **Accepted risk (owner, unchanged)** | Unchanged (F-28). |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Accepted risk — scope unchanged** | `admin_*` keys untouched; `settings.py`'s `OWNED_KEYS` guard still refuses any key it does not own. |
| B3 | Shared-folder credentials not logged | **Pass (by design: none exist)** | Unchanged. |
| B4 (added) | New pages and actions behind login and the launch cookie | **Pass** | `/settings/download`, `/settings/scheduling`, `/settings/email`, `/settings/application` (GET/POST) and `/events/<id>/cutoff` (POST) all use `login_required`; every POST needs the CSRF token (tested 403 without it). |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Local server transport | N/A at this stage | Unchanged; no new script, CSP unchanged. |
| C5 | Outbound connections and the read-only guarantee (BR 13) | **Pass** | No new network access; the AST guards over the whole code base still pass. |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D1 | **A second secret (the ACS connection string) is handled like the storage key** | **Pass** | Never echoed back to the page (tested); a blank field on save keeps the existing value rather than clearing it; shown only as "Saved" / "Not set"; never appears in an oplog or exception message (the rejection scrub strips anything in quotes, and the connection string is never placed in a message unquoted). |
| D2 | **Key-shaped text pasted into the wrong box is refused** | **Pass** | The email recipient field refuses a key-shaped value, same as the account/container fields (tested). |
| D3 | **The schema change cannot lose data** | **Pass** | `events.cutoff_timestamp` is added by an idempotent `ALTER TABLE ... ADD COLUMN` for a database created before Phase 10, in the same column position a fresh database's DDL uses, so the startup column-order check passes either way; run twice with no error; an existing row's other columns are untouched (tested by simulating a real pre-Phase-10 table). |
| D4 | **The per-event cut-off cannot affect another event** | **Pass** | Stored on the event's own row and looked up by that event's ID (case-insensitive, like every other event lookup); tested with two events sharing similar data. |
| D5 | **The shared retry setting cannot leak between runs** | **Pass** | `transfer.RETRY_COUNT` / `RETRY_DELAY` are read fresh at the start of every job and restored to their previous values in a `finally` block once the job ends, including when the job raises. Only one job runs per event, and a second start for the same event is refused (Phase 8); the application has one administrator and no concurrent-job-with-different-settings scenario is possible through the UI. |
| D6 | The read-only Application Settings page does not disclose anything beyond a local path | **Pass** | Shows only this computer's own database and log file paths, already implied by the application's own presence on the machine; no environment variable content, no other machine's information. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | All new settings values are parameterised in SQL, range/format-checked server-side (not relying on the HTML `min`/`max`/`maxlength` attributes, which a forged request bypasses - tested); the schedule day list is filtered to the fixed seven-value set, dropping anything forged. |
| E2 | Output encoding / XSS | **Pass** | Jinja auto-escaping for every new field. |
| E5 | Errors do not leak internals | **Pass** | Fixed plain messages; a refused save is a 400 with the same message shown to the operator and logged (scrubbed) as an exception. |
| E12 | Background job safety | **Pass** | The retry-settings mutation happens on the job's own thread before any file is touched, and is restored after, whether the job finishes normally, is cancelled, or raises. |
| E15 (added) | **The Dashboard's Test Connections reuses an existing, already-guarded route** | **Pass** | Posts `action=test-all` to the same `/events/<id>/mappings` endpoint Device Mapping's own "SAVE AND TEST ALL" uses; that endpoint only reads per-LED fields that are present in the POST body (`_entries`), so a Dashboard form that omits them changes nothing about the saved folders - it only runs the test, exactly like the existing button does. |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | `test_no_secrets` passes; no new dev/test configuration values. |
| F3 | Logs do not print secrets | **Pass** | See D1. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G2 | Failed actions logged without sensitive data | **Pass** | Every refused settings save is a categorised exception plus a Failed operational row, with typed values scrubbed. |
| G4 | Audit completeness and atomicity | **Pass** | Each settings save is one transaction with its audit row; the cut-off save and its audit row are one transaction. |
| G6 (added) | Log pruning is safe | **Pass** | Runs once at startup, before the startup row itself is written (so the very first row of a fresh run is never pruned); a failed prune goes to the diagnostic log only and never stops startup; it deletes by a UTC-comparable timestamp string, consistent with every other comparison in the application. |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | No new dependencies | **Pass** | Standard library only. |
| H2 | Supported runtime | Pass | Python 3.11.9. |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-62 | Info | The retry setting is a single mutable pair of module-level constants, restored per job rather than threaded as a parameter. Safe under this application's one-job-at-a-time model, but would need rework if a future phase ran two jobs concurrently with different settings. | Accepted; documented in the code. |
| S-2, S-4, S-9 | — | Accepted admin credential; accepted plain-text key (F-28); single-instance lock (Phase 12). | Carried. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | pending | pending |
| User (Vatsan) go-ahead | Vatsan | pending | pending |
