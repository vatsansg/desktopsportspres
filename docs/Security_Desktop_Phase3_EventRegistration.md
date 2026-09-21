# Security Checklist — Desktop Phase 3: Event Registration and GUID Validation

Release/hand-off: Phase 3 — Event Registration (Step 3.1, local test file) and GUID Validation (Step 3.2). Date: 21/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (only the two BRD-accepted items).

**Theme of this phase:** the event configuration file is the first *untrusted external input* the application accepts. Today it is a file chosen by the operator; from Phase 4 it is downloaded from Azure. It is treated as hostile either way.

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed / protected | **Pass** | No key used yet (Phase 4). Secret-scan test passes on the full committable tree. The shipped test files contain no secrets. |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Accepted risk — scope unchanged** | Registration code (`registration.py`, `exceptions.py`, `views_events.py`) never touches `application_settings` or the credential; an explicit test asserts it, and the Phase 1 scope-guard tests still pass. |
| B3 | Shared-folder credentials not logged | N/A at this stage | No shared-folder handling until Phase 5/8. |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Transport | N/A at this stage | Unchanged from Phase 1 (loopback only, exact Host, launch gate, CSRF, session epoch). All new routes are behind login and the launch cookie (tested). |
| C5 (added) | Untrusted URL inside the configuration | **Pass for Phase 3; open for Phase 4** | `eventStorageUrl` is validated as an https URL with a host and no embedded credentials, and is **never fetched** in this phase. **Phase 4 must require the host to equal the configured storage account** before any download (finding S-14). |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D1, D2 | Storage containers / encryption | N/A at this stage | No Azure access yet. |
| D3 | Values from the file are validated server-side before being stored | **Pass** | Every field is type-checked and length-bounded; GUID normalised; timestamps normalised to UTC; nothing is trusted from the client (including the `accept=.json` hint, which is cosmetic). |
| D4 | Uploaded content validated against type/size | **Pass** | Hard cap 60 000 bytes for the file (and 64 KB for the whole request → branded 413); UTF-8 required; strict JSON (no `NaN`/`Infinity`, no duplicate keys); deep nesting and 5 000-digit numbers rejected fast without crashing. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | All SQL parameterised. Typed Event ID restricted to `[A-Za-z0-9._-]`, 50 chars (also prevents path tricks when folders are created later). Jinja autoescaping everywhere; hostile names and typed IDs tested in the dashboard, forms, flash messages, error pages and the re-register page. |
| E1b (added) | **Spoofing / unsafe characters in text from the file** | **Pass (after fix)** | Review finding: only ASCII controls were rejected. Now rejected: all control characters (incl. C1/DEL), **lone surrogates** (previously a crash), line/paragraph separators, and the bidi override/embedding/isolate characters that can make a name *display* differently from how it is stored (e.g. `U+202E`). Legitimate Arabic, Persian (needs ZWNJ), Chinese, accents, emoji, ZWJ and LRM/RLM are accepted (tested). |
| E2 | Upload size limits | **Pass** | See D4. |
| E3 | Path traversal | **Pass** | The application never reads a path the client supplies (file *upload*, not a path; a `config_path` field is ignored — tested). The client file name is reduced to a sanitised base name (100 chars, no separators) before being recorded. |
| E4 | Session / token handling | **Pass** | Unchanged. New: the pending re-registration is referenced from the session only by a 192-bit random token; the cookie holds no GUID, name or file data (tested). |
| E5 | Errors do not leak internals or echo untrusted content | **Pass** | Messages contain only fixed text, regex-validated IDs, hex GUIDs and integers. File contents are never echoed (tested with a marker string in every field); a surrogate/hostile file gives a clear 400, never a 500 or stack trace. |
| E7 (added) | **Re-registration cannot be tricked** | **Pass (after fixes)** | (a) The operator must paste a GUID equal to the file's, and **the file's GUID is no longer shown in full** (only its last 6 characters), so it cannot simply be copied back — the paste proves it came from the web application. (b) The form is bound to the exact request it was shown for (page token in a hidden field, compared in constant time with the session's), so a second browser tab with an older screen cannot act on a newer request. (c) A new mismatch *replaces* the previous pending entry (no orphans). (d) Single use, 15-minute expiry, at most 8 held, in memory only. (e) Every wrong paste is logged as a GUID-validation exception. |
| E8 (added) | Denial of service by data | **Pass** | Size cap, 100-table cap (technical safety limit, not the business rule), nesting/number bombs, and repeated mismatches (bounded store) all handled without crash or unbounded memory. |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | Secret-scan test passes. |
| F2 | Remaining secrets | N/A at this stage | Storage key from Phase 4. |
| F3 | Logs do not print secrets or untrusted content | **Pass** | Exception and operation rows contain field names, reasons, IDs, GUIDs (not secrets) and the sanitised source label only — no file content, no control characters (tested). |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G2 | Failed/rejected actions logged without sensitive data | **Pass** | Every rejection → `exception_log` with BRD §21 category, operation, event, source, status *Open*. Operator typos and "no file chosen" are deliberately not logged as exceptions. |
| G4 (added) | Audit completeness and atomicity | **Pass (after fix)** | Review finding: the event row and its audit row were committed separately, so an event could exist without its audit trail. Now **one transaction** — both are saved or neither (tested by forcing the audit write to fail). Audit rows record the GUID(s) and source; a re-registration records `old GUID -> new GUID`. |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | No known-critical vulnerabilities in new dependencies | **Pass** | Phase 3 added **no new dependencies**. |
| H2 | Supported runtime | Pass | Python 3.11.9 (security support to Oct 2027). |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-14 | Medium (future) | `eventStorageUrl` from an untrusted file could steer a Phase 4 download — and any credential attached to it — to another host. | **Phase 4:** require host == configured storage account before any request. |
| S-15 | Medium → **Closed** | Lone surrogate in the event name crashed registration (HTTP 500, no exception-log row). | Fixed; tested. |
| S-16 | Low → **Closed** | Bidi override / separator / C1 characters accepted into names (display spoofing, log noise). | Fixed; legitimate scripts still accepted; tested. |
| S-17 | Medium → **Closed** | The re-register screen displayed the file's GUID, defeating its own cross-check. | Only the last 6 characters shown. |
| S-18 | Medium → **Closed** | Pending re-registration was bound to the session, not the page: a stale tab could act on a newer request. | Page token in a hidden field, constant-time compare; tested with two tabs. |
| S-19 | Low → **Closed** | Audit rows lacked the GUIDs and were committed separately from the change. | One transaction; richer rows. |
| S-20 | Low | GUID uniqueness across events has no database unique index (check-then-insert). Event IDs are case-sensitive while Windows folders are not. | Carried (Phase 4 / migration runner). |
| S-2 | Info | Accepted-risk plain-text admin credential. | Scope re-verified (B2). |
| S-4, S-9 | — | Storage-key protection at rest (Phase 4); single-instance lock (Phase 6/12). | Carried forward. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes** — no blockers; S-15 – S-19 fixed; the fixes did not receive a second independent pass (noted in the QA document, F-26) |
| User (Vatsan) go-ahead | Vatsan | 21/09/26 | Approved (manual test passed) |
