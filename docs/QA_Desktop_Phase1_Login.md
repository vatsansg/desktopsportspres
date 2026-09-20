# QA Test Case Document — Desktop Phase 1: Login

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 1 — Login (Step 1.1 Fixed admin login) |
| BRD reference(s) | Desktop BRD Section 6.1 (fixed admin, accepted-risk plain-text credential, changeable), 6.2 (login flow), 25 (login logged), 28.2 (upgrade keeps data) |
| Implementation Sequence reference | Desktop Implementation Sequence Step 1.1 |
| Date | 20/09/26 |
| Tested by | Claude Code (automated + scripted real-window run); user to run the manual guide below |
| Environment | Windows 11 Pro, 1280×800 display at 150% scaling, Python 3.11.9, pywebview 6.2.1, WebView2 153.x. No Azure access used. |

## How to test in the real app (for the user)

Use a scratch data folder so your real settings are never touched:

```powershell
cd C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop
$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test"
.\.venv\Scripts\python -m ledsync
```

Reset at any time: close the app, then delete the folder `%TEMP%\ledsync-test`.

| Step | Do this | Expect | Covers |
|---|---|---|---|
| 1 | Launch the app | A maximised window opens on a black **Sign In** screen; the cursor is already in Username; one orange button (**SIGN IN**), no other orange | TC-M01 |
| 2 | Type `admin` and a **wrong** password, press Enter (or click SIGN IN) | Red banner "Incorrect username or password."; fields are empty; what you typed is not shown anywhere | TC-M02 |
| 3 | Type a **wrong username** with the right password `Admin@123` | The same generic banner (it must not reveal which field was wrong) | TC-M03 |
| 4 | Type a password, click **Show**, then **Hide** | Text becomes visible, then hidden; the button label flips Show ⇄ Hide | TC-M04 |
| 5 | Sign in with `admin` / `Admin@123` | Dashboard opens: "Signed in as **admin**", **Change Password** and **Sign Out** buttons, a "No Events Yet" panel spanning the full width | TC-M05 |
| 6 | Click **Change Password** | The Current Password field is already focused. Try each of: wrong current password; new ≠ confirm; new shorter than 8 characters; new = current | Each shows its own clear red message and nothing changes | TC-M06 |
| 7 | Enter current `Admin@123`, new e.g. `Venue#2026`, same confirm, click **SAVE PASSWORD** | Back on the Dashboard with a green "Password changed." message | TC-M07 |
| 8 | Click **Sign Out**, then try `Admin@123`, then `Venue#2026` | Old password fails; new password signs in | TC-M08 |
| 9 | Sign out, enter a wrong password **5 times** in a row, then the **correct** password | Attempt 6 is refused: "Too many failed attempts. Try again in N seconds."; after ~30 s the correct password works | TC-M09 |
| 10 | Close the window with the X (top right); check Task Manager for leftover `python` processes; relaunch the same way and sign in with `Venue#2026` | No leftover process; new password still works (change persisted across restarts) | TC-M10 |
| 11 | Keyboard only: on the login screen press Tab through the fields; press Enter to submit | Tab order Username → Password → Show → SIGN IN, a blue focus ring is always visible | TC-M11 |
| 12 | Open `%TEMP%\ledsync-test\ledsync.db` in any SQLite viewer (e.g. DB Browser for SQLite). Look at `operation_log` and `application_settings` | `operation_log` has Application Startup, Login Failed/Blocked/Success, Password Change, Logout rows with UTC timestamps and **no password or typed username in any row**. `application_settings` holds `admin_username` / `admin_password` in plain text — this is the BRD 6.1 **accepted risk** | TC-M12 |
| 13 | Look at `%TEMP%\ledsync-test\logs\ledsync.log` | Startup lines only; no credentials and no `_launch?t=` token | TC-M13 |

Not testable by hand: the launch-token gate and CSRF (nothing in the app's own window can bypass them); they are covered by automated tests TC-A08 – TC-A14.

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 65 | 63 (48 automated TC-A01 – TC-A48 + 4 scripted real-window TC-W01 – TC-W04 + 11 manual TC-M01 – TC-M10 and TC-M12, run by the owner on 21/09/26: "all tested ok") | 0 | 0 | 2 manual not run (TC-M11 keyboard tab order and TC-M13 diagnostic-log check were not in the shortened guide the owner followed; both are covered by automated/scripted evidence) |

Automated: `.\.venv\Scripts\python -m pytest -q` → **119 passed** (Phase 0 and Phase 1 combined; TC-A35 – TC-A46 were added after the independent architect review). Real-window checks (wrong password → error, correct → dashboard, Change Password with Show/Hide, clean close) were executed by keystroke script on 20/09/26; screenshots are in `docs/screenshots/phase1-*.png`.

## Test cases — automated (pytest)

| ID | Description | Expected result | Status | Notes |
|---|---|---|---|---|
| TC-A01 | Seed creates the BRD default admin (`admin` / `Admin@123`) | Credentials verify | Pass | `test_auth.py` |
| TC-A02 | Seeding is idempotent and never overwrites a changed password | Changed password survives every relaunch/upgrade (BRD 28.2) | Pass | |
| TC-A03 | Credentials are case-sensitive; both fields must match; empty rejected; no rows → no login | Reject | Pass | |
| TC-A04 | Plain-text storage is asserted as the accepted risk | Fails loudly if the decision is changed accidentally | Pass | Documents BRD 6.1 |
| TC-A05 | Change-password rejections: wrong current / mismatch / < 8 / > 128 / same as old | `PasswordChangeError`, password unchanged | Pass | |
| TC-A06 | Only a wrong *current* password counts as a throttle failure | Flag set only then | Pass | |
| TC-A07 | Passwords with spaces and Unicode are accepted | Verify OK | Pass | |
| TC-A08 | Every page is 403 without the launch cookie (dashboard, login, change-password); static CSS/JS is exempt so the branded error page can render (TC-A40) | 403 | Pass | `test_views.py` — security |
| TC-A09 | POST without launch cookie is 403 | 403 | Pass | |
| TC-A10 | Wrong/missing launch token is 403; token is single-use | 403 on reuse | Pass | |
| TC-A11 | Session cookie is HttpOnly and SameSite=Strict | Flags present | Pass | |
| TC-A12 | POST /login, /logout, /account/password without or with a forged CSRF token | 403 | Pass | Security |
| TC-A13 | CSRF token accepted from the `X-CSRF-Token` header | 302 | Pass | |
| TC-A14 | CSRF token rotates on login (session-fixation defence) | Token differs | Pass | |
| TC-A15 | Unauthenticated `/` and `/account/password` redirect to `/login` | 302 | Pass | |
| TC-A16 | Login page is branded, has a password field and Show/Hide toggle | Present | Pass | |
| TC-A17 | Correct credentials → placeholder dashboard with "Signed in as admin", Sign Out, Change Password | 200 | Pass | Step 1.1 criterion |
| TC-A18 | Wrong password → 401 with authentication error, `role="alert"` | 401 | Pass | Step 1.1 criterion |
| TC-A19 | Wrong username gives the same generic error | Same message | Pass | No enumeration |
| TC-A20 | Failed login never echoes the typed username/password | Not in HTML | Pass | |
| TC-A21 | Failed login creates no session | `/` still redirects | Pass | |
| TC-A22 | Signed-in user visiting `/login` is redirected to the dashboard | 302 | Pass | |
| TC-A23 | 5,000-character inputs don't crash; oversized body (200 KB) → 413 | 401 / 413 | Pass | |
| TC-A24 | Throttle: 5 failures block even the correct password; released after 30 s; success resets count; count restarts after lock | 429 then 302 | Pass | Also unit-tested with a fake clock |
| TC-A25 | Wrong-current-password attempts on Change Password are throttled too | 429 | Pass | Prevents guessing via the second form |
| TC-A26 | Logout ends the session but the window stays usable; logout is POST-only | 302 / 405 | Pass | |
| TC-A27 | Change password: full flow; old password dead, new works | As expected | Pass | |
| TC-A28 | Changed password persists across a restart (seed never resets it) | Still valid | Pass | BRD 28.2 |
| TC-A29 | Each change-password validation error is shown and changes nothing | 400 + message | Pass | |
| TC-A30 | Operation log records Login Failed/Success, Logout, Password Change, Blocked; contains no credentials; ISO-8601 UTC timestamps | Rows as expected | Pass | BRD 25 |
| TC-A31 | **Scope guard:** no module except `services/auth.py` references the admin credential or keys; no other module reads `application_settings` | Empty offender list | Pass | Security B2 |
| TC-A32 | Startup seeds the admin and logs "Application Startup" (twice → two rows, no reset) | As expected | Pass | |
| TC-A33 | Orange appears only on `.btn-primary` in the stylesheet | Enforced | Pass | Brand rule |
| TC-A34 | No inline script/style/handlers in templates (CSP-safe) | None | Pass | |
| TC-A35 | **Session revocation on logout:** replaying a cookie captured before logout | Redirected to login (was 200 before the fix) | Pass | Architect review finding 1 |
| TC-A36 | **Session revocation on password change:** a captured cookie dies; the operator's own session stays signed in | Old cookie 302, current 200 | Pass | Finding 1 |
| TC-A37 | **Atomic throttle:** 60 simultaneous attempts → exactly 5 proceed, 55 refused | 5 / 55 | Pass | Finding 2 (threaded test) |
| TC-A38 | Typos in the *new* password (correct current password) never accumulate toward a lockout | 12 mismatches then success, never 429 | Pass | |
| TC-A39 | Lost/invalid cookie shows a branded "Session Not Valid — start it again" page (no Werkzeug text); styles still load | Branded 403 | Pass | Finding 3 |
| TC-A40 | Static assets are served without a session, but nothing else is (pages, `/_launch`, path traversal) | 403/404 | Pass | |
| TC-A41 | Oversized request shows a branded 413 | 413 + message | Pass | |
| TC-A42 | Only the exact `127.0.0.1:<port>` Host is accepted; `localhost`, no port, trailing dot, `[::1]`, wrong port all → 400 | 400 | Pass | Finding 4 |
| TC-A43 | Server does not advertise Werkzeug/Python versions (`Server: ledsync`); launch token absent from `repr()` | As expected | Pass | Findings 9, 11 |
| TC-A44 | Extra headers: CSP `object-src 'none'`, COOP, CORP, Permissions-Policy | Present | Pass | Finding 9 |
| TC-A45 | Password change is compare-and-set (a concurrent change is not silently overwritten) | "changed elsewhere" error, other value kept | Pass | Finding 5 |
| TC-A46 | Login error is linked to both inputs (`aria-describedby`); no `aria-pressed` on the Show/Hide toggle; a failed audit-log write never breaks the login; Werkzeug access log suppressed | As expected | Pass | Findings 8, 12, 13 |
| TC-A47 | WTT logo asset (`static/img/wtt-logo.png`) is a valid PNG and is served without a session (so the branded error page can show it) | 200 image/png | Pass | Added 21/09/26 at the owner's request |
| TC-A48 | Logo appears on the Sign In page, the dashboard header and the error page, with alt text "World Table Tennis (WTT)" | Present | Pass | Also checked visually in the real window |

## Real-window scripted run (executed 20/09/26)

| ID | Description | Result |
|---|---|---|
| TC-W01 | Wrong password in the real window → error banner, fields cleared, log shows `Login / Failed` | Pass |
| TC-W02 | Correct login → dashboard; log shows `Login / Success` | Pass |
| TC-W03 | Change Password page: first field auto-focused; Show/Hide reveals then hides typed text | Pass |
| TC-W04 | Window closes via the X cleanly; no leftover process | Pass |

## Open defects / follow-ups

| ID | Linked test case | Description | Severity | Status |
|---|---|---|---|---|
| F-02 | TC-M01 | System light title bar and default Python icon on the window frame. Dark title bar + WTT icon planned for the installer/UI polish phases. | Low | Open |
| F-03 | TC-M01 | Roboto Bold / Bio Sans not bundled (app must work offline); falls back to system fonts on machines without Roboto. Bio Sans files needed from the brand owner. | Low | Open — needs user input |
| F-10 | TC-A24 | Throttle is in memory: restarting the app clears it. Accepted for a venue-local single-admin tool; noted for completeness. | Info | Accepted |
| F-11 | — | Manual checks TC-M01 – TC-M10 and TC-M12 passed (owner, 21/09/26). TC-M11/M13 not run by the owner. | Info | Closed (2 not run) |
| F-06 | — | Per-launch token + CSRF (was carried from Phase 0) | — | **Closed in Phase 1** |
| F-12 | — | Review notes carried to later phases: (a) no single-instance guard — two app instances can overwrite each other's session cookie (cookies are not port-scoped on 127.0.0.1) → named mutex/lock file before Phase 6/12; (b) settings service that hard-excludes `admin_*` keys from any generic list/export, and loosening the "no other module mentions `application_settings`" guard test deliberately at Phase 4; (c) operation-log: local-time display conversion (BRD 25 wants DD/MM/YY HH:MM:SS), coalescing/retention of Blocked rows (Phase 9); (d) migration runner with pre-migration DB backup and a UI-free bootstrap shared with the headless scheduled entry point; (e) per-user `%LOCALAPPDATA%` vs headless/no-user-logged-in scheduling decision (Phase 12); (f) optional skip-link. | Low–Medium | Carried forward |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 20/09/26 | Approved with notes — no blockers; all "fix now" findings (session revocation, atomic throttle, branded 403, Host allow-list) fixed and covered by TC-A35 – TC-A46; remainder carried forward as F-12 |
| User (Vatsan) go-ahead | Vatsan | 21/09/26 | Approved — "All tested ok"; asked for WTT logo on login and dashboard header (done) and to commit and proceed |
