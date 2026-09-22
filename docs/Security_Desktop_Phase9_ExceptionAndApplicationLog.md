# Security Checklist — Desktop Phase 9: Exception Log and Application Log

Release/hand-off: Phase 9 — Error Handling, Exception Log, Application Log (Step 9.1, Step 9.2, the Logs page). Date: 22/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (BRD-accepted items and any the owner explicitly signs off).

**Theme of this phase:** the application now **shows its own logs and lets the administrator change and export them**. Three things must stay true: (1) a log never holds a secret — no key, password or value the administrator typed into the wrong box; (2) what a log shows or exports can never run code, redirect, or become a spreadsheet formula; (3) reviewing or exporting a log is an authenticated, CSRF-protected action of the administrator only, and a status change never destroys the record (nothing is deleted).

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed and protected | **Accepted risk (owner, unchanged)** | Unchanged (F-28). No log row, page or export carries the key (asserted for the operational log). |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Accepted risk — scope unchanged** | No new settings keys. The failed-login and password-change rows never hold a typed username or password (tested). |
| B3 | Shared-folder credentials not logged | **Pass (by design: none exist)** | Unchanged. |
| B4 (added) | New pages and actions behind login and the launch cookie | **Pass** | `GET /logs`, `GET /logs/export` and `POST /logs/exceptions/<id>/status` use `login_required` (403 without the launch cookie, redirect to sign-in when signed out); the status change needs the CSRF token, a valid status (400) and an existing exception (404). |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Local server transport | N/A at this stage | Unchanged. No new script (the page is server-rendered; the CSP still forbids inline script). |
| C5 | Outbound connections and the read-only guarantee (BR 13) | **Pass** | No new network access; the AST guards over the whole code base still pass. |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D1 | **Logs never hold a typed value or secret** | **Pass (after a review fix)** | A refused settings or mapping save is logged with its fixed reason only: anything between quotes (a typed value, possibly a pasted key) is replaced by `'...'` and the text is capped at 300 characters (tested with a key-shaped value in an IP box). One validation message (a UNC path with no share name) embedded the typed host **unquoted**, so it survived the scrub - fixed to quote it like every sibling message (review finding S-60, tested with a host name). Run-level rows carry counts only. Failed-login rows say "Authentication failed." |
| D2 | **No log row is destroyed** | **Pass** | A status change updates one column and writes an audit row (*Exception Reviewed*); nothing is deleted, pruned or rewritten (owner decision: keep everything). |
| D3 | **Automatic resolution cannot hide a different problem** | **Pass** | An exception is resolved only when the **same** event, operation, table, LED type, file, source and destination succeeds later (fields not supplied must be empty in the row); a different file, device, table or event stays Open (tested). Resolution never deletes the record. |
| D4 | Output safety | **Pass** | Jinja auto-escaping for every column (a `<script>` message is shown as text — tested); the review form's hidden filter fields are re-escaped values, and the redirect after a review is rebuilt from a **whitelist** of filter names through `url_for` (no open redirect, no arbitrary parameter). |
| D5 | **CSV export is spreadsheet-safe** | **Pass** | Every cell passes the change-log neutraliser (a value starting `= + - @`, tab, carriage return or a full-width variant gets a leading apostrophe — tested with a `=HYPERLINK(…)` message); a byte-order mark makes UTF-8 open correctly; `Cache-Control: no-store`; fixed download names; at most 50,000 rows. |
| D6 | Queries are parameterised and bounded | **Pass (after a review fix)** | An astronomically large `page` (or a negative/huge offset passed directly to `query()`) caused an unhandled `OverflowError` binding to SQLite (a 500, not a security hole, but a page a single malformed link could break - review finding S-61); page and offset are now clamped. Filters are bound parameters; the free-text filter escapes `%` and `_` (tested as literal text); category and status values are checked against the known lists; page, date and tab input that is not valid is ignored, never an error. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | See D6; the event filter is a bound, case-insensitive comparison; the review id is an integer route parameter. |
| E2 | Output encoding / XSS | **Pass** | See D4. |
| E5 | Errors do not leak internals | **Pass** | A failure to write a log row goes to the diagnostic file only and never stops the operation it describes (a run's Started / finished rows are written on their own short connection and can never fail a run). |
| E12 | Background job safety | **Pass** | The run-level rows are written outside the run's own transaction; a run that stops unexpectedly still records *Failed*. |
| E14 (added) | Export is an audited read | **Pass** | Exporting writes a *Log Export* row (count of rows). It is a GET with no data change beyond that audit row and needs the signed-in session; an unrelated page cannot read the file (same-origin loopback server, launch cookie). |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | `test_no_secrets` passes; no new configuration values. |
| F3 | Logs do not print secrets | **Pass** | See D1. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G1 | Every BRD Section 21 category can be recorded | **Pass** | All ten categories are accepted; an unknown one is refused (tested). |
| G2 | Failed actions logged without sensitive data | **Pass** | Registration, cloud test, device test, change check, download, synchronisation and refused configuration saves each write a categorised exception row and a *Failed* operational row. |
| G3 | Section 25 event types | **Pass with carry-forward** | Startup, login (success, failed, blocked), logout, password change, event registration, configuration changes, mapping saves, device tests, cloud tests, change checks, run start / finish, every file download and push, exception review and log export are written. **Scheduled executions** (Phase 12) and **application updates** (Phase 13) have no source yet (F-63). |
| G4 | Audit completeness and atomicity | **Pass** | Each per-file row is written in the same transaction as the outcome it describes (unchanged); an exception's auto-resolution is part of the same transaction as the success that caused it. |
| G5 | Timestamps | **Pass** | Stored in UTC; shown as DD/MM/YY HH:MM:SS in local time; every test row falls inside the time window of the test. |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | No new dependencies | **Pass** | Standard library only. |
| H2 | Supported runtime | Pass | Python 3.11.9. |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-58 | Info | Scheduled executions and application updates are not logged yet (features not built). | Phases 12 / 13. |
| S-60 | Medium -> **Closed** | (Review) A refused UNC device folder with no share name logged the typed host unquoted, bypassing the rejection scrub. | The host is now quoted in that message, like every other validation branch. |
| S-61 | Low -> **Closed** | (Review) An extreme `page` value in the URL raised an unhandled `OverflowError` (500) on the Logs page. | `page` and the computed offset are clamped before they reach SQLite. |
| S-59 | Info | The exception status has no free-text note and logs are never pruned (owner decision). A retention period can be a Phase 10 setting. | Accepted. |
| S-2, S-4, S-9 | — | Accepted admin credential; accepted plain-text key (F-28); single-instance lock (Phase 12). | Carried. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 22/09/26 | **Approved with notes after fixes** - see S-60, S-61 (both reproduced and fixed; 3 regression tests; **not re-reviewed**). |
| User (Vatsan) go-ahead | Vatsan | pending | pending |
