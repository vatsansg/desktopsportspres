# Security Checklist — Desktop Phase 2: Event Dashboard

Release/hand-off: Phase 2 — Event Dashboard (read-only). Date: 21/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (only the two BRD-accepted items).

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed / protected | **Pass** | No key used yet (Phase 4). Secret-scan test passes on the full committable tree. |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Accepted risk — scope unchanged** | Phase 2 added `services/events.py`, which reads the `events` table only; it never touches `application_settings`. The scope-guard tests (`test_credential_only_touched_by_auth_module`, `test_only_auth_module_reads_admin_rows_from_application_settings`) still pass, so no module other than `services/auth.py` references the credential. |
| B3 | Shared-folder credentials not logged | N/A at this stage | No shared-folder handling until Phase 5/8. |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Transport | N/A at this stage | Unchanged from Phase 1: loopback only, exact Host, launch gate, CSRF, session epoch. The dashboard route remains behind the login and the launch cookie (regression-tested: 302 signed-out, 403 without cookie). |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D1–D4 | Storage / upload validation | N/A at this stage | Read-only page; no Azure, no uploads. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | The page renders database text (event name, ID, status, timestamp) that can originate from Azure `_GUID.json` (Phase 3+) or hand edits, so it is treated as untrusted. Jinja autoescaping covers all four cells (tested with `<script>` and `<img onerror>` payloads); `status_key` (used in a CSS class) comes only from a fixed internal set so it cannot inject markup; SQL is one static `SELECT` with no parameters from input; the `configuration_json` blob is deliberately **not** selected. |
| E2, E3 | Upload limits / path traversal | N/A at this stage | No user-supplied paths. |
| E4 | Session handling | **Pass** | Unchanged (Phase 1). |
| E5 | Errors do not leak internals | **Pass** | **Improved this phase:** an unexpected exception now renders a branded page with no exception text, path or stack trace (tested with a message containing an internal path); previously only 400/403/404/405/413 were branded. |
| E6 | Robustness against malformed data (availability) | **Pass** | Architect review BLOCKER: one row with an odd timestamp (e.g. `1969-12-31T23:59:59Z`) crashed the whole dashboard (HTTP 500). Fixed: only 1970–2098 is accepted as a date, conversion cannot raise, each row is isolated, out-of-range values are shown as raw text. Tested with 20 hostile values × 3 zones and end-to-end through the page; a second independent re-check (46 values × 3 zones, plus invalid-UTF-8 text, which was found and fixed) confirmed it. |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | Secret-scan test passes. |
| F2 | Remaining secrets | N/A at this stage | Storage key from Phase 4. |
| F3 | Logs do not print secrets | **Pass** | The only new log line is a skipped-row error containing the event ID, never credentials or event configuration. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G1–G3 | Logging | N/A at this stage / Pass | Viewing the dashboard is not audited (read-only, no state change). Row-level display failures go to the diagnostic log. |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | No known-critical vulnerabilities in new dependencies | **Pass** | Phase 2 added **no new dependencies**. |
| H2 | Supported runtime | Pass | Python 3.11.9 (security support to Oct 2027). |

## I. Developer tooling (added this phase)

| # | Item | Status | Notes |
|---|---|---|---|
| I1 | The dev helper that inserts FAKE events cannot damage real data | **Pass (after fix)** | Review finding: the first version only required `LEDSYNC_DATA_DIR` to be non-empty and used `INSERT OR REPLACE`, so pointing it at the real data folder could have overwritten a real event (GUID and configuration included). Now: refuses an unset variable; refuses the application's real data folder; refuses a folder that does not exist unless `--create`; uses plain `INSERT` and never overwrites an existing event ID; marks its rows so `--clear` removes only those. All tested; the re-check also tried relative paths, trailing slashes, upper case, forward slashes, `..` segments, 8.3 short names and a junction to the real folder — all refused, nothing created. The script lives in `scripts/` and is not part of the shipped application. |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-11 | Medium → **Closed** | One malformed timestamp could take the whole event list down (denial of service by data). | Fixed and tested (E6). |
| S-12 | Medium → **Closed** | Dev script could overwrite a real event if aimed at the real data folder. | Fixed and tested (I1). |
| S-13 | Low → **Closed** | Unhandled errors showed the stock server error page. | Branded 500 page, no internals (E5). |
| S-2 | Info | Accepted-risk plain-text admin credential. | Scope re-verified (B2). |
| S-4, S-9 | — | Storage-key protection at rest (Phase 4); single-instance lock (Phase 6/12). | Carried forward. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agents (two fresh contexts) | 21/09/26 | First pass **Rejected** (S-11 blocker, reproduced); fixed; focused re-check **Approved with notes** (residual invalid-UTF-8 hole found and fixed) |
| User (Vatsan) go-ahead | | | Pending |
