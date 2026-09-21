# Security Checklist — Desktop Phase 6: Change Log & Incremental Comparison

Release/hand-off: Phase 6 — Change Log and Incremental Download Logic (Step 6.1 retrieve/parse, Step 6.2 local change log, Step 6.3 comparison). Date: 21/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (BRD-accepted items and any the owner explicitly signs off).

**Theme of this phase:** the application now reads a **file that other people control** (the web application's change log in cloud storage) and its contents will decide, in Phase 7, **which files are written to this computer and what they are called**. Two things must stay true: nothing in that file can make the application read or write outside its own folders, and reading it never changes anything. This phase downloads **no assets** and deletes **no files**.

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed and protected | **Accepted risk (owner, unchanged)** | Unchanged (F-28). The new code receives an already-built read-only client and never handles the key; the change-log page and its errors contain no key (tested, and re-checked against the real account). |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Accepted risk — scope unchanged** | The new modules touch neither `admin_*` nor `cloud_*` settings. |
| B3 | Shared-folder credentials not logged | **Pass (by design: none exist)** | Unchanged (Phase 5 owner decision: Windows login the application runs under). |
| B4 (added) | New pages behind login and the launch cookie | **Pass** | `/events/<id>/changes` and `/events/<id>/changes/check` use `login_required`; tested signed-out and without the launch cookie; CSRF token required on the POST (a refused POST contacts Azure zero times — tested). |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Local server transport | N/A at this stage | Unchanged. |
| C5 | Outbound connections | **Pass** | Only the existing read-only Azure client; no new library or connection. The AST guards that forbid write/SAS operations, private SDK attributes, dynamic attribute access, other HTTP clients and Azure imports outside `storage.py` all still pass with the new modules. Tests use a fake that **raises if any write operation is attempted**; a check makes only listing and download calls. |
| C6 | Untrusted storage address inside `_GUID.json` (S-14) | **Pass (re-applied)** | Every check re-verifies the address in the file against the account/container/folder it was downloaded from before the change log is trusted (tested with an address pointing at another account). |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D3 | Values from Azure validated before use | **Pass** | Size capped **in the request itself** (5 MB + 1 byte); 50,000-row cap; strict UTF-8, NUL refused; header must contain the required columns. **Each path** is checked before it can reach any later phase and is **never trimmed** (spaces at either end are refused): not empty, ≤ 200 characters, each name ≤ 255, no control, bidi, zero-width or space-lookalike characters, no `\ : < > " | ? *`, not absolute, no empty/`.`/`..` segments, no name that ends in a dot or space or starts with a space, and no Windows device name (`CON PRN AUX NUL COM0-9 LPT0-9 CONIN$ CONOUT$`, also with an extension). **Each timestamp** must parse (1970–2098); each status must be New/Updated/Deleted. Bad rows are skipped, **counted and shown** (fixed wording, never echoing the row) — the rest of the file is still used. |
| D8 (added) | **Only paths inside the event's own tables and LED types are acted on** | **Pass** | A path must be exactly `Table N/<Inner\|Outer\|MainLED>/<file>` for a table and LED type the event enables; anything else (another folder, sub-folders, a table not in the event) is listed as *Not applicable* and can never be queued. |
| D9 (added) | **The check changes nothing** | **Pass** | Tested: after a check the database tables `download_history`, `sync_history`, `led_mappings`, `events` are byte-identical, the data folder holds no new file, and the only writes are one `operation_log` row (counts only) and, on failure, one `exception_log` row. |
| D10 (added) | **Windows-unsafe file names (for Phase 7)** | **Pass (after fix); Phase 7 must still re-validate** | The independent review showed that names legal in a URL but dangerous on NTFS were accepted (`a.png.` overwrote `a.png`; `b.png:s` hid a stream; `NUL`, `com1.txt`). They are now refused and reported, with 22 dangerous and 17 ordinary names tested. Phase 7 must still re-validate at the point of writing, refuse links and stay inside the event's own folder (QA F-46). |
| D11 (added) | **One file, one entry** | **Pass (after fix)** | The same file written two ways (`Table 01`, `Table 0001`, Arabic-Indic digits) is not merged and not downloaded twice — only `Table N` with ASCII digits and no leading zero is a table. Case is ignored for A–Z only; names that Windows could treat as one file but differ in accents, Unicode form or `ß`/`ss` are **listed as not applicable and never queued**, so nothing is silently hidden or overwritten. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | SQL parameterised. Event ID from the URL re-validated; result held per event under a case-folded key. |
| E2 | Output encoding / XSS | **Pass** | Jinja auto-escaping; `<script>`, attribute-breakout and formula-style file names render as text (tested). No inline script/style (CSP unchanged). |
| E5 | Errors do not leak internals | **Pass** | Every failure is a fixed plain message with a BRD §21 category; Azure/SDK text, the account name and the key are never shown or stored (tested with a hostile exception message). |
| E11 (added) | **Denial of service by a huge or crafted file** | **Pass** | 5 MB and 50,000-row caps; the download request itself is capped so a huge blob is never fully read; on-screen lists capped at 500 rows (counts always full). |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | `test_no_secrets` passes; no new configuration values. `scripts/insert_test_history.py` inserts pretend rows only, and refuses the real data folder. |
| F3 | Logs do not print secrets | **Pass** | *Check Changes* audit rows hold counts and the file name of the log only; exception rows hold the fixed message. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G2 | Failed actions logged without sensitive data | **Pass** | Missing change log (*Missing folder*), re-exported event (*GUID validation*), unusable file (*Configuration*), Azure problems (*Azure Storage connectivity* / *Permission*) — each one exception row plus a *Failed* audit row. |
| G4 | Audit completeness | **Pass** | Every check (success or failure) is audited. |
| G5 (added) | **The local change log file cannot run code** | **Pass** | In `_localchangelog.csv` any cell starting with `= + - @`, tab or carriage return is prefixed with an apostrophe, so a spreadsheet cannot treat a file name as a formula (tested). The file is written atomically (temporary file, then replace); if it cannot be replaced (open in Excel) the previous version stays and the application carries on — the database is the record. |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | No new dependencies | **Pass** | Standard library only (`csv`, `io`, `unicodedata`). Runtime closure unchanged (24 packages). |
| H2 | Supported runtime | Pass | Python 3.11.9. |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-37 | Medium → **Closed** | (Review) File names unsafe on NTFS (trailing dot, `:stream`, device names, illegal characters, over-long) passed the parser as normal downloads; the reviewer reproduced an overwrite and a hidden stream in a probe. | Rejected per name segment and reported; never trimmed; tested. Phase 7 re-validates at the write site (F-46). |
| S-39 | Medium → **Closed** | (Review) Several spellings of one table folder, and `casefold`-equal names Windows keeps apart, created duplicate downloads or hid a file behind another's deletion. | ASCII-only case folding, ASCII table numbers with no leading zero, collision detection; tested. |
| S-40 | Low → **Closed** | (Review) A database error emptied `_localchangelog.csv` while reporting success; an unexpected error gave an error page with no audit or exception row; a saved result outlived a re-registration or new history. | Existing file left untouched; every failure is a plain message with an exception row and audit row (a logging failure never hides it); results are dropped when the GUID or history changes; tested. |
| S-41 | Info | Cloud entries dated far in the future would be taken as the latest (the comparison never looks at the clock, by owner decision). | Pointed out on the page ("dated more than a day in the future"); behaviour unchanged. |
| S-38 | Info | The change log's `username` column (a person's e-mail address in the real log) is read but never stored or shown. | None needed. |
| S-2, S-4, S-9 | — | Accepted admin credential (scope re-verified); accepted plain-text key (F-28); single-instance lock (Phase 6/12). | Carried. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes** — no blockers; findings S-37, S-39, S-40 fixed and regression-tested (83 tests); fixes not given a second independent pass |
| User (Vatsan) go-ahead | Vatsan | | *pending manual test* |
