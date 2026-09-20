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
- **Status:** Complete (owner's manual test passed and go-ahead given 21 September 2026; built and reviewed 20 September; committed `47e3461`, merged to `main` `47ca4f7`; logo addition committed on 21 September — see table)
- **Completed on:** 21 September 2026
- **What was built:**
  - **Step 1.1:** WTT-branded Sign In screen (username autofocus, Show/Hide password toggle, single orange `SIGN IN`), placeholder Dashboard ("Signed in as admin", Change Password, Sign Out, full-width "No Events Yet" panel), Change Password screen. Fixed administrator `admin` / `Admin@123` seeded on first run into `application_settings` **in plain text (BRD 6.1 accepted risk)**; password changeable (current password + ≥ 8 chars + confirmation); a changed password survives restarts/upgrades (seed never overwrites).
  - **Owner decisions applied (20 Sep 2026):** credential lives in `application_settings`; only the password is changeable; short throttle (5 failures → 30 s); no idle timeout; no forced first-login change.
  - **Security (closes Phase 0's carried F-06):** loopback server gated by a one-time launch token exchanged for a signed HttpOnly SameSite=Strict session cookie; CSRF token on every POST; session fixation defence; server-side session epoch so logout and password change revoke previously issued cookies; atomic throttle; exact `127.0.0.1:<port>` Host only; branded 403/404/405/413 pages; extra security headers; no server version advertised; WebView2 private mode; launch token never logged; Werkzeug access log suppressed.
  - **Logging:** login success/failure/blocked, logout, password change and application startup written to `operation_log` (UTC ISO timestamps, never any credentials; audit-write failures never break a login).
  - **Window:** now opens maximised (a fixed 800px-high window overshot the 752px working area on this 1280×800 display).
  - 117 automated tests pass (Phase 0 + 1); real-window keystroke run captured screenshots `docs/screenshots/phase1-*.png`.
- **Owner follow-up (21 Sep 2026):** added the WTT logo (`reference-docs/WTT-Logo.png` → `ledsync/web/static/img/wtt-logo.png`) to the Sign In screen, the signed-in header (with the app name beside it) and the error page.
- **QA Test Case doc:** `docs/QA_Desktop_Phase1_Login.md` — 65 cases: 63 pass (48 automated, 4 real-window, 11 manual run by the owner), 2 manual not run (keyboard tab order, diagnostic-log check); 119 automated tests pass.
- **Security Checklist:** `docs/Security_Desktop_Phase1_Login.md` (B2 scope guard verified by test: only `services/auth.py` touches the credential)
- **Independent architect review:** Approved with notes, no blockers. Fixed now: session revocation (demonstrated by cookie replay), atomic throttle, branded dead-end 403, `localhost` removed from Host list, compare-and-set password update, best-effort audit writes, extra headers, accessibility (`aria-describedby`, toggle semantics), explicit private mode. Carried forward (QA doc F-12 / Security S-9): single-instance lock, settings service that excludes `admin_*`, log display/retention, migration runner + UI-free bootstrap, data-dir vs headless decision.
- **`ralph-loop` / `wtt-brand` polish:** run (2 of max 3 iterations used; completion promise met). Changes: orange now used only for the primary button (removed decorative bar/dot), hover colour tokenised, input styling made type-agnostic; enforced by a test.
- **Deviations:**
  1. Added non-brand functional tokens (`--status-error`, `--status-ok`, neutral surface/border greys) because the WTT palette defines no error/success colour or control borders; borders are deliberately strong for contrast on black (the owner's web-app feedback).
  2. `operation_log` timestamps stored as UTC ISO; display conversion to DD/MM/YY HH:MM:SS is a later-phase task. This fixes UTC for this log only and does **not** settle the open BRD §36 timezone decision for change-log comparison.
  3. Static CSS/JS is exempt from the launch gate (public source) so the branded error page can render.
  4. Fonts (Roboto Bold/Bio Sans) still not bundled — Bio Sans files needed from the brand owner (QA F-03).

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
| 1 | Complete | 21 Sep 2026 | `47e3461` (merge `47ca4f7`); logo follow-up: see git log |
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
