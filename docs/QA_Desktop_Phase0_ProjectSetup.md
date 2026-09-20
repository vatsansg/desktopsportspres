# QA Test Case Document — Desktop Phase 0: Project Setup

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 0 — Project Setup (Step 0.1 repository/environment scaffold, Step 0.2 empty SQLite schema) |
| BRD reference(s) | Desktop BRD Section 5 (Technical Architecture), Section 26 (SQLite Database Requirements), Section 28.2 / Business Rule 9 (DB preserved), Section 21.1, Section 7.1 (schema extensions) |
| Implementation Sequence reference | Desktop Implementation Sequence Step 0.1, Step 0.2 |
| Date | 20/09/26 |
| Tested by | Claude Code (automated + scripted manual); user to repeat manual cases TC-01 – TC-04 |
| Environment | Windows 11 Pro 10.0.26200, Python 3.11.9 venv, pywebview 6.2.1, Edge WebView2 runtime 153.x. No Azure access used in this phase. |

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 41 | 40 | 0 | 0 | 1 (TC-24, truly clean machine — see follow-up F-01) |

Cases marked **Auto** are covered by `pytest` (`tests/`, 41 tests, all passing). TC-25 – TC-41 were added after the independent architect review. Cases marked **Manual** were executed by script on the development machine and should be repeated by the user by hand.

## Test cases

| ID | Description | Preconditions | Steps | Expected result | Actual result | Status | Notes |
|---|---|---|---|---|---|---|---|
| TC-01 | App launches and shows a window (Manual) | venv built; WebView2 present | 1. `python -m ledsync` <br>2. Observe | A native window titled "LED Asset Download & Sync" opens on a black surface with a white headline and one orange accent bar | Window opened 1280×800, correct title and branding (`docs/screenshots/phase0-blank-window.png`) | Pass | |
| TC-02 | Closing the window exits cleanly (Manual) | App running | 1. Close the window with the X (scripted `CloseMainWindow`) | Process exits within 10 s; log shows "Application closed"; no leftover ledsync process | Exited; "Application closed" logged; 0 leftover processes | Pass | |
| TC-03 | Auto-close smoke run exits 0 (Manual) | — | 1. `python -m ledsync --auto-close 6` | Window opens, closes after 6 s, exit code 0, DB created | Exit code 0, ~10 s elapsed, DB created | Pass | |
| TC-04 | Launch from a fresh venv built only from `requirements.txt` (Manual) | New venv, runtime deps only (no dev deps) | 1. `pip install -r requirements.txt` <br>2. `python -m ledsync --auto-close 4` | Launches and closes cleanly | Exit code 0 | Pass | Closest available proxy for "clean machine"; see TC-24 |
| TC-05 | Server binds loopback only, random port (Auto) | — | Start server; inspect `server_address` | `127.0.0.1`, port > 0 | As expected (`test_server_binds_loopback_only_and_serves`) | Pass | |
| TC-06 | Two instances never collide on a port (Auto) | — | Start two servers | Different ports | Different ports (`test_two_instances_get_different_ports`) | Pass | Does not prevent two *app* instances; single-instance handling is a later-phase note |
| TC-07 | Index page renders branded shell (Auto) | — | GET `/` | 200; contains app name and `wtt.css` | As expected | Pass | |
| TC-08 | WTT colour tokens defined once as CSS variables (Auto) | — | GET `/static/css/wtt.css` | `--wtt-black/white/orange/blue/teal` with exact hex values | As expected | Pass | Brand check |
| TC-09 | Security headers present (Auto) | — | GET `/` | nosniff, X-Frame-Options DENY, CSP `default-src 'self'`, no-store | As expected | Pass | |
| TC-10 | Foreign Host header rejected — DNS-rebinding defence (Auto) *(security-adjacent)* | Server running | Send request with `Host: evil.example.com` | HTTP 400 | 400 | Pass | |
| TC-11 | Stopping the server releases the thread and the port (Auto) | — | `stop()`; then bind the same port | Thread ended; port re-bindable | As expected | Pass | |
| TC-12 | Unknown route returns 404 (Auto) | — | GET `/does-not-exist` | 404 | 404 | Pass | |
| TC-13 | DB has exactly the seven Section 26 tables (Auto, independent `sqlite3` check) | Fresh data dir | `init_db`; list `sqlite_master` | events, led_mappings, download_history, sync_history, application_settings, operation_log, exception_log — nothing else | As expected | Pass | Verified without using app code |
| TC-14 | Every Section 26 column present, in order (Auto) | — | Compare `PRAGMA table_info` to the BRD list | All BRD columns present in BRD order | As expected | Pass | Expected lists were typed from BRD Section 26 independently of `schema.py` |
| TC-15 | Only the two approved extensions exist beyond Section 26 (Auto) | — | Diff actual columns vs BRD | Extras = `events.configuration_json` and the five `exception_log` §21.1 columns only | As expected | Pass | Extensions approved by owner 20/09/26 |
| TC-16 | All tables empty after init (Auto) | — | `SELECT COUNT(*)` on each | 0 | 0 | Pass | Step 0.2 "no data" |
| TC-17 | Re-running init preserves existing data (Auto) *(BRD 28.2 / Rule 9)* | Row inserted | Run `init_db` again | Row still present | Present | Pass | Upgrade-safety foundation |
| TC-18 | Schema version recorded via `PRAGMA user_version` (Auto) | — | Read pragma | `1` | 1 | Pass | |
| TC-19 | Database from a newer app version is refused, not altered (Auto) | `user_version` set to 2 | `init_db` | `SchemaError`, no changes | Raised | Pass | Downgrade protection |
| TC-20 | Foreign key enforced on `led_mappings` (Auto) | — | Insert mapping for unknown event | `IntegrityError` | Raised | Pass | |
| TC-21 | Exception log accepts an unregistered event ID (Auto) | — | Insert exception for event `9999` | Succeeds | Succeeds | Pass | Needed for rejected GUID registration (Step 3.2) |
| TC-22 | Missing parent directory is created for the DB (Auto) | — | `init_db` on nested path | DB created | Created | Pass | |
| TC-23 | No secret-shaped string in any committable file (Auto) *(negative / security)* | — | Scan tracked + untracked-not-ignored files | None found; `.env`, `.env.local`, `OBSERVATIONS.txt` are git-ignored | Pass; and the guard was proven to fail on a planted 88-char base64 string | Pass | Covers Security B1/F1 |
| TC-24 | Launch on a genuinely clean Windows machine with no Python/dev tools (Manual) | Clean VM / second PC | Install and launch | Opens and closes cleanly | — | Not run | Cannot be proven before the Phase 13 installer exists. Also needs WebView2 runtime check |
| TC-25 | Host check fails closed (Auto) *(security, added after architect review)* | Freshly created app | GET `/` before any host is configured | 400 | 400 | Pass | Previously skipped the check while unset |
| TC-26 | `localhost:<port>` Host accepted by the real server (Auto) | Server running | Request with `Host: localhost:<port>` | 200 | 200 | Pass | |
| TC-27 | Security headers present on error responses too (Auto) | — | GET unknown route | nosniff + CSP with `frame-ancestors 'none'` | As expected | Pass | |
| TC-28 | CSP includes `base-uri 'none'` and `form-action 'self'` (Auto) | — | GET `/` | Directives present | As expected | Pass | Prepares for Phase 1 forms |
| TC-29 | Per-launch random `SECRET_KEY` (Auto) | — | Create two apps | Keys differ, ≥ 32 chars, not hard-coded | As expected | Pass | Groundwork for Phase 1 sessions |
| TC-30 | Per-request DB connection, closed at request end (Auto) | DB initialised | Use `get_db()` in an app context; use it after the context exits | Same connection within request; unusable after | As expected | Pass | Each threaded request gets its own connection |
| TC-31 | SQLite opens with WAL, 5 s busy timeout, foreign keys on (Auto) | — | Read PRAGMAs | `wal`, `5000`, `1` | As expected | Pass | Lets a scheduled run overlap the UI (Phase 12) |
| TC-32 | WebView2 missing → plain-language dialog, exit 1, DB untouched (Auto, simulated) | Detection stubbed to "absent" | `main([])` | One dialog naming WebView2; return 1; no DB file created | As expected | Pass | Real absence not reproducible on this machine (runtime installed); detection logic itself covered by TC-33/34 |
| TC-33 | WebView2 detection reads the real registry (Auto) | This Windows machine | `webview2_version()` | Non-empty version | Non-empty | Pass | |
| TC-34 | WebView2 detection: absent → None, present → version (Auto) | Registry read stubbed | Call function | `None` / version string | As expected | Pass | |
| TC-35 | Database created by a newer app version → dialog, log entry, exit 1, not altered (Auto) *(BRD 28.2)* | `user_version` = 2 | `main([])` | Dialog mentions "newer version"; `logs\ledsync.log` contains the stack trace; return 1 | As expected | Pass | Startup failures are visible under `pythonw` |
| TC-36 | Unwritable data directory fails gracefully (Auto) | Data dir path's parent is a file | `main([])` | Dialog, exit 1, no crash | As expected | Pass | |
| TC-37 | Schema drift detected at startup (Auto) | Extra column added | `_verify` | `SchemaError` | Raised | Pass | |
| TC-38 | Diagnostic file log created under `<data>\logs` (Auto/Manual) | — | Run app | `ledsync.log` exists with startup lines | Exists (6 lines after a 5 s run) | Pass | Separate from the Section 25 operational log |
| TC-39 | Data-dir resolution: env override, `%LOCALAPPDATA%\LEDAssetSync`, home fallback (Auto) | — | `default_data_dir()` under each condition | Correct path each time | As expected | Pass | |
| TC-40 | Importing `ledsync.main` does not load pywebview/pythonnet (Auto) | — | Import in a subprocess; inspect `sys.modules` | `webview` absent | Absent | Pass | Keeps a future headless scheduled run UI-free |
| TC-41 | Importing `ledsync.__main__` does not start the app (Auto) | — | Import in a subprocess | Returns immediately, exit 0 | As expected | Pass | |

## Manual verification by the user (please repeat)

1. `cd Applications\desktop` then `.\.venv\Scripts\python -m ledsync` — window appears; close it with the X; confirm no `python` process remains in Task Manager.
2. Run the app once, then open `%LOCALAPPDATA%\LEDAssetSync\ledsync.db` in any SQLite viewer (e.g. DB Browser for SQLite): confirm seven tables, all empty.
3. Compare the columns to BRD Section 26; the only additions should be `events.configuration_json` and five `exception_log` columns (marked `-- EXT` in `ledsync/db/schema.py`).

## Open defects / follow-ups

| ID | Linked test case | Description | Severity | Status |
|---|---|---|---|---|
| F-01 | TC-24 | "Clean Windows machine" validation cannot be fully proven until the Phase 13 installer exists. Venue machines must have the WebView2 runtime; installer must check for / bootstrap it. | Medium | Carried to Phase 13 |
| F-02 | TC-01 | Window uses the system light title bar and the default Python icon. Dark title bar + WTT icon deferred to UI polish (Phase 10) / installer (Phase 13). | Low | Open |
| F-03 | TC-08 | Roboto Bold and Bio Sans font files are not bundled (app must work offline). Display currently falls back to installed system fonts. Bio Sans files needed from the brand owner. | Low | Open — needs user input |
| F-04 | TC-06 | Nothing prevents two app instances running against one SQLite DB. Needed before Phase 12 (scheduled run vs. manual run). | Medium | Carried to Phase 8/12 |
| F-05 | — | `ralph-loop` polish pass not run: Phase 0 has no real UI (placeholder window). Will run from Phase 1 onward. | Info | Awaiting user agreement |
| F-06 | TC-25 | Per-launch access token + HttpOnly/SameSite=Strict cookie and CSRF protection for state-changing endpoints (a random port alone is obscurity — a hostile web page or local process could still post to the loopback server). Must exist before any POST endpoint. | Medium | Carried to Phase 1 |
| F-07 | — | UI-free headless entry point for Task Scheduler runs (`--run-scheduled`), plus data-dir/account decision and named-mutex single-instance lock. | Medium | Carried to Phase 8/12 |
| F-08 | — | Migration runner (`version < SCHEMA_VERSION` steps, transactional, with pre-migration DB backup) before the first upgrade ships; index `event_id` on history/log tables; decide `events.event_guid` uniqueness. | Low | Carried to Phase 3/13 |
| F-09 | — | Real (not simulated) WebView2-absent behaviour needs a machine without the runtime. | Low | Carried to Phase 13 |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 20/09/26 | Approved with notes — no blockers; 5 should-fix-now items resolved (one via a different fix than proposed, see workflow.md), remainder carried forward as F-06 – F-09 |
| User (Vatsan) go-ahead | | | Pending |
