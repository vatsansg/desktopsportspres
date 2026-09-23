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
| D3 | **The schema change cannot lose data** | **Pass** | `events.cutoff_enabled` / `events.cutoff_time` are added by idempotent `ALTER TABLE ... ADD COLUMN` statements for a database created before Phase 10, in the same column position a fresh database's DDL uses, so the startup column-order check passes either way; run twice with no error; an existing row's other columns are untouched (tested by simulating a real pre-Phase-10 table). |
| D4 | **The per-event cut-off cannot affect another event** | **Pass** | Stored on the event's own row and looked up by that event's ID (case-insensitive, like every other event lookup); tested with two events sharing similar data. |
| D7 (added) | **The recurring cut-off cannot silently lose a file** | **Pass** | A change timestamped after the boundary is held, never discarded: it is recomputed fresh on every check from the database and the current UTC clock, with nothing marked "skipped forever" anywhere, so it is downloaded or removed automatically once a later boundary passes it (tested: a held entry becomes eligible under a later boundary with no other state change). The emergency "Enable cut-off" switch bypasses it entirely and is off by default. |
| D5 | **The shared retry setting cannot leak between runs** | **Pass (after a review fix)** | The saved setting is loaded once per job and passed down as an **ordinary argument** through every engine (`transfer.run`, `assets.process`, `rpi.process`, `sync.process`) - not a shared mutable global. An earlier version mutated `transfer.RETRY_COUNT` / `RETRY_DELAY` around each job and restored them afterwards; because only *same-event* jobs are blocked from running together (Phase 8), two jobs for **different** events on their own threads could observe or permanently corrupt each other's value - reproduced on real threads by the review (S-62 promoted to a real finding and closed; see below). With no global left to mutate, the race is structurally impossible, not just unlikely. |
| D6 | The read-only Application Settings page does not disclose anything beyond a local path | **Pass** | Shows only this computer's own database and log file paths, already implied by the application's own presence on the machine; no environment variable content, no other machine's information. |
| D8 (added) | **The new boundary-status line discloses nothing sensitive and cannot disagree with the real decision** | **Pass** | It renders `changes.cutoff_boundary()`'s own instant - the exact value the held/eligible decision already uses - as a UTC ISO string in a data attribute, auto-escaped by Jinja like every other field; nothing about this is secret (an operator who can see the event page can already see the saved cut-off time). It is never re-derived independently in JS, so the displayed status and the real behaviour cannot drift apart. Absent entirely when there is no active boundary (cut-off off, or no time saved), so it never states a boundary that isn't real. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | All new settings values are parameterised in SQL, range/format-checked server-side (not relying on the HTML `min`/`max`/`maxlength` attributes, which a forged request bypasses - tested); the schedule day list is filtered to the fixed seven-value set, dropping anything forged. |
| E2 | Output encoding / XSS | **Pass** | Jinja auto-escaping for every new field. |
| E5 | Errors do not leak internals | **Pass** | Fixed plain messages; a refused save is a 400 with the same message shown to the operator and logged (scrubbed) as an exception. |
| E12 | Background job safety | **Pass (after a review fix)** | The retry setting is loaded once at the start of the job and passed down as a plain argument (see D5) - nothing shared is mutated, so there is nothing to restore and nothing a concurrent job could see mid-flight. |
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
| S-62 | **Medium → Closed** | (Review) The retry setting was a mutable pair of module-level constants (`transfer.RETRY_COUNT`/`RETRY_DELAY`), mutated and restored around each job. Two jobs for **different** events run concurrently (Phase 8 only blocks a second job for the *same* event); the reviewer reproduced both cross-contamination (one job briefly observing another's in-flight value) and a permanent leak (the wrong "old" value winning the restore race) with a standalone repro. | Removed the global entirely: `retries`/`delay` are now ordinary parameters threaded through `transfer.run` / `assets.process` / `rpi.process` / `sync.process` / `_retry`, loaded once per job in `downloads._run_job`. Two threads with different values, run concurrently, now provably never see or leave behind each other's setting (new regression test). |
| S-63 | Low → **Closed** | (Review) An out-of-range cut-off year (e.g. 2099, outside the application's accepted 1970-2098) gave the same message as a genuinely unreadable value, which could read as "try again" rather than "pick an in-range year". | A year outside the accepted range now gets its own message. |
| S-64 | Info → **Closed** | (Review note) The cut-off field's UTC framing relied entirely on label/hint text; the native `datetime-local` picker shows no time-zone indicator, a plausible real-world source of a local-time mistake. | A live UTC clock next to the field now shows "this computer's current UTC time", refreshed every 30 s, via the existing CSP-safe `app.js` (no inline script). |
| S-2, S-4, S-9 | — | Accepted admin credential; accepted plain-text key (F-28); single-instance lock (Phase 12). | Carried. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review (first build, retry/settings scope) | Independent review agent (fresh context) | 22/09/26 | **Approved with notes after a fix** - see S-62, S-63, S-64 (S-62 reproduced on real threads and fixed by removing the mutable global; 4 regression tests). Predates the cut-off redesign below; did not review the recurring cut-off logic. |
| Independent Solution Architect review (recurring cut-off redesign) | Independent review agent (fresh context) | 22/09/26 | **Approved, no findings.** The critical property - a held file can never become permanently stuck or silently lost - was verified with an executable probe tracing the full pipeline (a held assessment is structurally filtered out before the download engine ever runs, so nothing is written to `download_history`, and the push engine only ever reads rows that exist). Boundary math, the full held/eligible decision table, the emergency-override bypass, web validation/CSRF, CSP-safe JS and the two-column schema upgrade were all verified sound. A permanent end-to-end regression test for the no-stuck-file property was added (it previously stopped at the comparison layer). Two cosmetic, accepted notes: an unusual mislabelled reason text in one edge case, and a fails-safe (not a security) UX rough edge when disabling with an unsaved malformed time. |
| User (Vatsan) go-ahead | Vatsan | 23/09/26 | **"All good"** — the one finding from real use (F-71, QA doc) was a UX/wording issue, not a security defect; fixed and re-verified by Claude, see D8. |
