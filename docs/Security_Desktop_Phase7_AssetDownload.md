# Security Checklist — Desktop Phase 7: Asset Download

Release/hand-off: Phase 7 — Asset Download (Step 7.1 download, Step 7.2 local structure, Step 7.3 local change log). Date: 21/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (BRD-accepted items and any the owner explicitly signs off).

**Theme of this phase:** the application now writes **many files, some very large, whose names and content come from cloud storage** into folders on this computer, on a background thread, and deletes local files when the cloud says so. Three things must stay true: nothing a remote name can say lets a write or delete land outside the chosen folders (never beside the database), a file is only kept if it is exactly what Azure holds, and a failure or cancel never leaves a half file under a real name. Azure is still only ever read. Nothing is sent to the LED devices in this phase.

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed and protected | **Accepted risk (owner, unchanged)** | Unchanged (F-28). The download code receives an already-built read-only client; the key appears in no page, summary, history row or log (asserted in the live tests). |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Accepted risk — scope unchanged** | The new setting keys (`rpi_folder`, `asset_folder`) belong to `settings.py`; `admin_*` keys are untouched and other keys are still refused (tested). |
| B3 | Shared-folder credentials not logged | **Pass (by design: none exist)** | Unchanged. |
| B4 (added) | New pages and actions behind login and the launch cookie | **Pass** | `download`, `cancel` and `progress` use `login_required`; both POSTs need the CSRF token; tested signed-out, without the launch cookie, with an unknown event and without a token. A GET never starts a download (tested). |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Local server transport | N/A at this stage | Unchanged. The progress page adds one small script file (`static/js/changes.js`, same origin — the CSP still forbids inline script). |
| C5 | Outbound connections and the read-only guarantee (BR 13) | **Pass** | Only the existing read-only client. New reads use **only** `walk_blobs` (listing) and `download_blob(offset, length)` with `.readall()` — the AST guards that forbid write/SAS operations, private attributes, dynamic access and other HTTP clients all still pass; the fake raises on any write-shaped call; every test asserts the calls made are listing and download only. Reads are pinned to the listed version (`If-Not-Modified`), which is still a read. |
| C6 | Untrusted storage address (S-14) | **Pass (re-applied)** | Every download job starts with a fresh check that re-verifies the address and the GUID before anything is fetched. |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D3 | Values from Azure validated before use | **Pass** | Names from the change log **and** from Azure's own listing pass the same validators (no `..`, absolute or backslash path, no invisible characters, no `:` stream, device names, 8.3 aliases, trailing dot/space, over-long names) and are refused if they are a type that can run code or make Windows contact another computer (`.exe .lnk .url .scf desktop.ini …`). Ignored unsafe names are **counted and shown** to the operator. |
| D4 | Downloads bounded | **Pass** | Reads are 4 MB ranges (nothing large is ever held in memory); free disk space is checked first; a run handles at most 2,000 files; Azure listings are cached per run. |
| D5 | The probe/temporary file cannot damage anything | **Pass** | A download is written to a hidden temporary file (`.<32 hex>.ledsync-tmp`, exclusive create), flushed to disk (fsync) and only then moved into place; a failed write, wrong download or cancel removes it; stale ones left by a crash or closed window are swept from the folder where they were made (exact name pattern only — a user's own file of a similar name is never touched). |
| D6 | **Local writes and deletes are confined** | **Pass (after review fixes)** | Destination folders are built only from names this application controls (`<Event ID>`, `Table N`, `Inner/Outer/Main LED`), each validated; every level must be a plain folder — an existing file, symbolic link or **junction** with that name is refused **on the write path and the removal path** (the review showed removal followed a junction; fixed and tested with real junctions). The roots (asset, RPI) are refused if they are, contain or alias the data folder, a drive root, an operating-system folder, or a place where the database appears as a plain file — by file **identity**, not spelling. Path length is checked explicitly. |
| D7 | **Only the right files are acted on** | **Pass (after review fixes)** | Tables and LED types the event does not enable are never listed, downloaded or given a folder. The same file written two ways, or two Azure names that would be one file on disk (`ß`/`ss`, case, accents, the same name in `mainled` and `Main LED`) are **never merged and never offered** — they are reported. The change log always wins over Azure's list. |
| D8 | Integrity | **Pass** | Every file must equal Azure's listed **size** and, when Azure lists one, its **MD5**; a mismatch discards the file, retries once and reports; the previous copy is untouched. A file replaced in Azure **during** the download is detected (version pinning) and the retry re-lists — even when the size is unchanged and no MD5 exists (both tested). Checksums use `usedforsecurity=False` (works on FIPS-mode Windows). |
| D9 | **Deletions are safe** | **Pass** | A cloud removal deletes only a plain file with a validated name inside the event's own Table/LED (or RPI) folder; never a folder, link or junction; empty folders are left; a removal for a file never stored is recorded without error. |
| D10 | RPI files of different events cannot clash | **Pass** | `<RPI folder>\<Event ID>\<file>` (owner decision) — one event's download, update or removal never touches another event's file (tested). |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | Event ID from the URL re-validated and used as a folder name only after the folder-name check; both folder settings validated like device folders (absolute path, no OS folders, not inside the data folder, no streams/device names/invisible characters), must be different, neither inside the other, neither containing the data folder — compared by real location (8.3 names and junctions resolved). |
| E2 | Output encoding / XSS | **Pass** | Jinja auto-escaping for file names, paths and summary lines; summary severity is structured (not guessed from text). |
| E5 | Errors do not leak internals | **Pass** | Fixed plain messages with BRD §21 categories; SDK text, account name and key never shown; an unexpected error in the job is a plain message (the traceback goes to the local log file only). |
| E8 | The application does not hang | **Pass** | Folder operations (creation, existence checks, replace, disk-space) run under time limits; the page bounds its disk checks (8 s in total); streaming writes are cancellable between chunks. |
| E12 (added) | **Background job safety** | **Pass (after review fixes)** | One job per event (double-submit refused); the job has its own database connection (WAL, per-file commits); the summary is stored **before** the state says finished (a race that lost it was found and fixed); a cancel or connection problem ends the job at once with an accurate count of files not tried; a crash ends in an error state, never a stuck one. |
| E13 (added) | Azure being busy | **Pass (after review fixes)** | HTTP 429/500/502/503/504 are treated like a lost connection: one retry, then the run stops instead of failing thousands of files slowly. |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | `test_no_secrets` passes; no new configuration values; the live tests never print the key. |
| F3 | Logs do not print secrets | **Pass** | History rows hold the blob address (no credential), local path and outcome; audit rows hold file names and counts; exception rows hold the fixed message. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G2 | Failed actions logged without sensitive data | **Pass** | Each failed file → a Failure history row, an *Asset Download* / *RPI Files* audit row and an exception row (category, table, LED type, file name). A file gone from both disk and Azure is recorded once as removed (a review finding: it used to add a Failure row every run). |
| G4 | Audit completeness and atomicity | **Pass** | Each file's history row and audit row are one transaction; `last_download` is updated after successful downloads; `_localchangelog.csv` is rewritten atomically after every job (never emptied by a database error). |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | No new dependencies | **Pass** | Standard library and the existing Azure SDK only. |
| H2 | Supported runtime | Pass | Python 3.11.9. |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-47 | Medium → **Closed** | (Review) A cloud removal followed a junction at the Table or LED folder level and deleted a file outside the asset tree. | Removal now walks the folders level by level and refuses any link; tested with a real junction. |
| S-48 | Medium → **Closed** | (Review) Azure-only files in a `Main LED` folder were offered but could never be downloaded (a Failure every run); the same name in two Azure folders was silently dropped. | Real Azure paths are used; clashes are reported and not offered; tested. |
| S-49 | Low → **Closed** | (Review) Stale temporary files in sub-folders were never swept, and the sweep would have deleted a user file with a similar name. | Swept where they are made, exact name pattern only. |
| S-50 | Low → **Closed** | (Review) A finished job's summary could be lost in a race; a file missing everywhere added a Failure row every run; a retry reused a stale listing; a file replaced mid-download could be mixed when Azure lists no MD5; Azure 429/5xx were not treated as a stop; a stop/cancel ran a slow tidy-up; the settings nesting rule ignored junction/8.3 aliases and did not refuse a folder containing the data folder. | All fixed and regression-tested (34 tests). |
| S-51 | Info | Streaming writes have no overall time limit (a dead network destination could hold the worker inside a write; cancel is honoured between chunks); the summary is consumed by the first page view (like a flash); an Azure-only file overwritten in Azure without a log entry is not re-downloaded (its history holds Azure's time). | Accepted / documented (QA F-51 – F-53). |
| S-2, S-4, S-9 | — | Accepted admin credential; accepted plain-text key (F-28); single-instance lock (Phase 6/12). | Carried. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes** — no blockers; five "fix now" findings (S-47 – S-50) fixed and regression-tested (34 tests) with the worthwhile notes; fixes not given a second independent pass |
| User (Vatsan) go-ahead | Vatsan | | *pending manual test* |
