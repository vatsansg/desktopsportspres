# LED Asset Download and Synchronisation Application — Workflow / Progress Tracker

**This is a living document.** Claude Code must amend this same file after every phase is completed (not create a new file) — updating status, completion date, what was actually built, and a link to that phase's QA Test Case and Security Checklist documents.

Status values: `Not started` / `In progress` / `Awaiting user go-ahead` / `Complete`.

---

## Phase 0 — Project Setup
- **Status:** Complete (user go-ahead 20 September 2026; committed `a35cfe2` and pushed to `main`)
- **Completed on:** 20 September 2026
- **What was built:**
  - **Step 0.1:** standalone git repo in `Applications/desktop` (`origin` = `desktopsportspres`, branch `phase-0-project-setup`; `.gitignore` written before anything else); Python 3.11 venv; package `ledsync/` split into `web/`, `services/`, `db/`, `config.py` per BRD §5. Flask served on a loopback-only random port in a background thread and shown in a native pywebview (Edge WebView2) window; WTT-branded placeholder (black surface, Roboto Bold headline, one orange accent, brand tokens as CSS variables). Clean shutdown on window close (verified: no leftover process). Startup failures show a dialog and write `<data>\logs\ledsync.log`; WebView2 absence is detected up front. `--auto-close N` flag for smoke tests.
  - **Step 0.2:** SQLite DB with the seven §26 tables (`ledsync/db/schema.py`), idempotent creation, existing data never touched, schema version via `PRAGMA user_version`, newer-version DBs refused, schema drift detected at startup; WAL + 5 s busy timeout + foreign keys; per-request connection helper.
  - Security groundwork: `Host`-header check (fails closed), security headers/CSP, per-launch `SECRET_KEY`, `tests/test_no_secrets.py` guard, `pip-audit` clean on the 15-package runtime closure.
  - 41 automated tests passing; real-window launch/close verified by script (screenshot `docs/screenshots/phase0-blank-window.png`).
- **QA Test Case doc:** `docs/QA_Desktop_Phase0_ProjectSetup.md` (41 cases: 40 pass, 1 not run — TC-24, true clean machine, needs the Phase 13 installer)
- **Security Checklist:** `docs/Security_Desktop_Phase0_ProjectSetup.md`
- **Independent architect review:** Approved with notes, no blockers. Fixed now: WebView2 pre-check, startup-failure visibility (dialog + log file), lazy `webview` import and guarded `__main__`, WAL/busy-timeout/per-request DB, Host check fails closed + per-launch secret key + tighter CSP, `*.pfx/*.pem/*.key` ignored, tests added for gaps. Carried forward: per-launch token/CSRF (Phase 1), headless entry point + single-instance lock + data-dir/account decision (Phase 8/12), migration runner (Phase 3/13).
- **Deviations:**
  1. **Schema extended beyond BRD §26 with owner approval (20 Sep 2026, "Extend now"):** `events.configuration_json` (BRD §7.1) and five `exception_log` columns — `table_number`, `led_type`, `file_name`, `source`, `destination` (BRD §21.1). All marked `-- EXT` in `schema.py`. Also added, not in §26: `UNIQUE(event_id, table_number, led_type)` on `led_mappings` and foreign keys from `led_mappings`/`download_history`/`sync_history` to `events` (logs deliberately have none, so a rejected registration can be logged).
  2. **`ralph-loop` not run** for this phase: Phase 0 has no real UI to polish. `wtt-brand` was applied to the placeholder window. Will run from Phase 1 — user to confirm.
  3. **Reviewer's proposed WebView2 fix (`gui="edgechromium"`) was not used:** pywebview's own source shows it still falls back silently to MSHTML when the runtime is missing, so the app checks the registry itself and refuses to start instead.
  4. **Font files (Roboto Bold, Bio Sans) not bundled** — app must run offline; falls back to system fonts. Bio Sans files needed from the brand owner.
  5. **"Clean machine" validation** only approximated (fresh venv from `requirements.txt`) until the installer exists.
- **Security finding S-1 — resolved 20 September 2026:** a live Storage Account key sat in plaintext in the parent repo's `references/0forimplementation/desktop/OBSERVATIONS.txt` (never committed). On the owner's instruction that file is now git-ignored in the parent repo (`.gitignore` edited; parent repo not committed by Claude). Owner has not (yet) decided on key rotation. The file's contents were not modified.

## Phase 1 — Login
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 2 — Event Dashboard (SQLite only)
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 3 — Event Registration and GUID Validation
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 4 — Azure Storage Connectivity
- **Status:** Not started
- **Includes:** first live connection to the (confirmed) Storage Account using the access key.
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 5 — LED Structure and Device Mapping
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 6 — Change Log and Incremental Download Logic
- **Status:** Not started
- **Includes:** full-path matching for `_ledassetchangelog.csv` entries (v2.4 note — multiple `sponsorsequence.csv` per event).
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 7 — Asset Download
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 8 — Push / Synchronise to LED Devices
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 9 — Error Handling, Exception Log, Application Log
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 10 — Application Settings and Dashboard Polish
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 11 — Email Notification
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 12 — Scheduled Operation
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 13 — Installer and Upgrade Handling
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 14 — Full Workflow and Acceptance Validation
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

---

## Overall status

| Phase | Status | Go-ahead date | GitHub commit |
|---|---|---|---|
| 0 | Complete | 20 Sep 2026 | `a35cfe2` |
| 1 | Not started | | |
| 2 | Not started | | |
| 3 | Not started | | |
| 4 | Not started | | |
| 5 | Not started | | |
| 6 | Not started | | |
| 7 | Not started | | |
| 8 | Not started | | |
| 9 | Not started | | |
| 10 | Not started | | |
| 11 | Not started | | |
| 12 | Not started | | |
| 13 | Not started | | |
| 14 | Not started | | |
