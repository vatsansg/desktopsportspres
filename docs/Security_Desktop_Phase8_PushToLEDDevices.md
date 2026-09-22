# Security Checklist — Desktop Phase 8: Push To LED Devices

Release/hand-off: Phase 8 — Push to LED Devices (Step 8.1 push, Step 8.2 Download & Sync with Operation Status). Date: 21/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (BRD-accepted items and any the owner explicitly signs off).

**Theme of this phase:** the application now **writes files onto other machines' folders** (the LED devices' shared folders, reached with the Windows login the application runs under) and **removes files from them**, from a background thread. Three things must stay true: (1) it writes and removes only inside the folder the operator mapped, and only files it is entitled to touch; (2) a copy is proven identical before it takes its name, so a device never shows a half-written or corrupt file; (3) one dead or hostile device never blocks, corrupts or leaks into the others, and no credential is ever stored or shown.

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed and protected | **Accepted risk (owner, unchanged)** | Unchanged (F-28). The push works from local files and needs no key at all; the key appears in no page, summary, history row or log (asserted in the live test on `sync_history`). |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Accepted risk — scope unchanged** | No new settings keys. |
| B3 | Shared-folder credentials not logged | **Pass (by design: none exist)** | Owner decision: the Windows login the application runs under is used. No user name, password or connection string for a device is stored, asked for or logged. |
| B4 (added) | New pages and actions behind login and the launch cookie | **Pass** | `sync`, `sync/push`, `sync/cancel` (POST) and `operations/progress` (GET) use `login_required`; POSTs need the CSRF token; tested signed-out, without the launch cookie, with an unknown event (404) and without a token. |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Local server transport | N/A at this stage | Unchanged. The Dashboard adds one small script (`static/js/dashboard.js`, same origin — the CSP still forbids inline script). |
| C5 | Outbound connections and the read-only guarantee (BR 13) | **Pass** | The push touches **no** Azure API (a push-only run works with the cloud settings removed and makes zero Azure calls — tested). The AST guards that forbid write/SAS operations, dynamic attribute access and other network imports still pass over the whole code base. |
| C6 | Device paths are local/UNC filesystem paths only | **Pass** | Reached with ordinary file operations under the operator's Windows login; no new sockets or protocols. |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D1 | **Writes and removals are confined to the mapped folder** | **Pass** | The file name comes from the download history and is re-validated at the write site (no separators, `..`, `:` streams, device names such as `NUL`, 8.3 aliases, trailing dot/space, dangerous types such as `.exe`/`.lnk`/`.scf`); the target is the device folder plus one plain name; a folder or link with that name is refused (tested). |
| D2 | **The device folder cannot be the application's own data or downloads** | **Pass (after review fixes)** | A device folder may not be, contain or sit inside the local **asset folder or RPI folder** (checked when saving a mapping and again at push time, by file identity) — otherwise a push could overwrite the downloaded originals and a cloud removal delete them (review blocker S-52). `check_destination` also compares by file identity: the data folder, any parent of it, an OS folder, a drive root and a **junction** to the data folder are refused before anything is written (tested with a real junction). A missing folder is reported and **never created**. |
| D3 | **Integrity: a device never shows a bad file** | **Pass** | The file is copied to a hidden temporary name on the device (`.<32 hex>.ledsync-tmp`), flushed (fsync), **read back and compared by size and MD5** with the original, and only then moved over the real name. A mismatch discards the temporary file and leaves the old file on the device (tested with a device that silently loses half the data). |
| D4 | **Only the right files are removed** | **Pass (after review fixes)** | A cloud deletion removes a file from a device only when `sync_history` shows this application pushed it to that exact device path (and has not since removed it — a later failed push or failed removal does not hide that); a folder that another mapping (of any event) also uses is never cleaned; files that were on the device already, or put there by anyone else, are never removed (tested). Removing a file already gone is a success. |
| D5 | A same-named file this application did not put there is replaced | **Accepted (owner decision)** | The device must show what the cloud says; the replacement is atomic (verified copy, then move) and audited. A read-only file on the device gives a specific message and is left as it was. |
| D6 | Bounded | **Pass (after review fixes)** | A copy that makes no progress for 60 s is abandoned as a network failure for that device (the abandoned copy removes its own temporary file); a device check that times out fails only that device. Also: Copies are streamed in 4 MB pieces (nothing large is held in memory); free space on the device is checked first; a run handles at most 5,000 items; folder operations and the final move run under time limits; cancel is honoured between pieces and leaves nothing behind. |
| D7 | Per-device isolation | **Pass** | Each destination is tested first; a dead device fails only its own files (recorded as failures with a plain reason), is tested once, and other devices carry on. After a network failure the rest of that device's files are not attempted. |
| D8 | The local change log and history do not disclose secrets | **Pass** | `sync_history` holds file name, full device path, time and outcome; `_localchangelog.csv` gets a Sync Status column; neither holds a credential. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | Event ID from the URL is looked up (unknown → 404) and used only from the database's own spelling; device paths are validated when saved (Phase 5) and again at use. |
| E2 | Output encoding / XSS | **Pass** | Jinja auto-escaping for event IDs, file names, paths and summary lines; the polling script builds the panel with `textContent` only. |
| E5 | Errors do not leak internals | **Pass** | Fixed plain messages with BRD §21 categories; OS error text for a device is mapped to a category message; an unexpected error in the job is a plain message (the traceback goes to the local log file only). |
| E8 | The application does not hang | **Pass** | See D6; a dead network share cannot block the request (the work is on the background thread) and folder checks are time-limited. |
| E12 | **Background job safety** | **Pass** | One job per event (double start refused, tested); the job has its own database connection; the summary is stored before the state says finished; the operations endpoint returns counters only. |
| E13 | Event status cannot hide a problem | **Pass** | Status is computed from real state (device tests, downloads, pushes) and shows **Attention needed** until a later success resolves the failure; refresh never raises into the request. |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | `test_no_secrets` passes; no new configuration values; the live tests never print the key. |
| F3 | Logs do not print secrets | **Pass** | Audit rows hold file names and counts; exception rows hold the category, table, LED and file name. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G2 | Failed actions logged without sensitive data | **Pass** | Each failed file → a Failure `sync_history` row, a *Synchronise* audit row and an exception row (category *Synchronisation*, *Network Share Access*, *Permission* or *Missing Folder*); a dead device gets one exception, not one per file. |
| G4 | Audit completeness and atomicity | **Pass** | Each file's history row and audit row are one transaction; `last_sync` is updated after successful pushes; `_localchangelog.csv` is rewritten atomically after every job. |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | No new dependencies | **Pass** | Standard library only. |
| H2 | Supported runtime | Pass | Python 3.11.9. |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-52 | **Blocker → Closed** | (Review) A device folder overlapping another LED's local asset folder (possible when the asset folder is outside the data folder) let a push overwrite the downloaded originals, and a cloud removal then delete them. | Overlap with the asset or RPI folder refused at save and at push (tested: inside, equal, containing). |
| S-55 | Medium → **Closed** | (Review) A device check that timed out aborted the whole run; a failed removal was never retried and left the status stuck; a stalled copy could freeze the run; removals could hit a folder shared with another mapping. | Every check failure is a per-device error; "we put it there" is derived from the whole history; copies are watched (60 s stall); shared folders are never cleaned. |
| S-56 | Low → **Closed** | (Review) Event status showed Synced with an unmapped LED, could not clear after a folder change, and counted RPI-only downloads; the Operation Status re-announced every second. | Status computed from what is still planned for current folders; live region only on the status line. |
| S-57 | Info | A removal does not re-verify the device file; a same-second re-download is not re-pushed; files pushed to an old folder are not removed after a mapping change; a dead device writes a failure row per file. | Accepted. |
| S-53 | Info | The application does not watch devices; a file removed from a device by hand is not re-sent. | Accepted — design. |
| S-54 | Info | Real SMB share behaviour was exercised with local folders, a real junction and simulated failures; no real venue share was available. | Accepted; the owner's `C:\LED\…` folders cover the layout. |
| S-2, S-4, S-9 | — | Accepted admin credential; accepted plain-text key (F-28); single-instance lock (Phase 12). | Carried. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes after fixes** — see S-52, S-55, S-56 (all reproduced by running code and fixed; 11 regression tests; **not re-reviewed**). |
| User (Vatsan) go-ahead | Vatsan | 22/09/26 | Approved after the manual test. |
