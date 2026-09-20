# Security Checklist — Desktop Phase 1: Login

Release/hand-off: Phase 1 — Login. Date: 20/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (only the two BRD-accepted items).

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed / protected in local configuration | **Pass** | No key is used yet (Phase 4). `tests/test_no_secrets.py` still passes on the full committable tree; `.env*` and `OBSERVATIONS.txt` are git-ignored in this repo, and the parent repo now ignores the key file too (S-1 closed in Phase 0). |
| B2 | Accepted-risk plain-text admin credential has not silently expanded in scope | **Accepted risk — scope confirmed unchanged** | The credential (BRD 6.1) is stored in plain text in `application_settings` (`admin_username`, `admin_password`) by owner decision. **Scope verified:** (a) only `ledsync/services/auth.py` references the credential or its keys, enforced by `test_credential_only_touched_by_auth_module` and `test_only_auth_module_reads_admin_rows_from_application_settings`; (b) it is used only for the local login and the change-password screen — never for Azure, shared folders or email; (c) it is never written to any log (`test_login_events_are_logged_without_any_credentials`, `test_password_change_is_logged_without_the_passwords`); (d) it is never echoed to the browser (`test_failed_login_does_not_echo_what_was_typed`). It is changeable by the administrator (BRD 6.1 requirement) and the change survives restarts/upgrades. |
| B3 | Shared-folder credentials not logged in plaintext | N/A at this stage | No shared-folder handling until Phase 5/8. |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Web-app transport items | N/A at this stage | Desktop equivalent — layers on the local server, all tested: (1) binds `127.0.0.1` only, random port; Host header must be exactly `127.0.0.1:<port>` — `localhost` is deliberately excluded because it can resolve to `::1` where another process could own the same port (fails closed); (2) **launch gate**: every page needs a signed session cookie obtained by exchanging a one-time token that only the app's own window receives — a hostile web page or another local process guessing the port gets 403; static CSS/JS is exempt (public source) so the branded error page can render; (3) CSRF token on every state-changing request; (4) server-side **session epoch** so logout/password change revoke every previously issued cookie. Plain HTTP on loopback is by design. The server does not advertise Werkzeug/Python versions. |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D1–D4 | Storage / upload validation | N/A at this stage | Not applicable to login. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | All SQL is parameterised (`?` placeholders); no string-built SQL from input. Templates use Jinja autoescaping; no `|safe`. Input lengths capped (username 100, password 128; request body 64 KB → 413). |
| E2 | Upload size limits | N/A at this stage | No uploads. Global 64 KB request-body cap applies. |
| E3 | Path traversal | N/A at this stage | No user-supplied paths yet. Remains critical for Phase 7 (change-log paths are untrusted input). |
| E4 | Session/authentication handling | **Pass** | Signed session cookie: HttpOnly, SameSite=Strict, per-launch random `SECRET_KEY` (never persisted, so every restart invalidates sessions), session cleared and CSRF token regenerated on login (fixation defence). **Server-side revocation (added after architect review):** signed cookies cannot be deleted server-side, so each carries a session epoch that is bumped on logout and on password change — a cookie captured before either is rejected (tested by replay). WebView2 runs in private mode (set explicitly), so no cookies, form data or password autofill are persisted by the browser engine. No idle timeout **by owner decision** (session ends when the window closes). Not `Secure`-flagged because the server is plain HTTP on loopback. |
| E5 | Error messages do not leak internals | **Pass** | Login failure is one generic message for wrong username or password. 400/403/404/405/413 use default bodies. Throttle message reveals only the wait time. |
| E6 (added) | Brute-force resistance | **Pass** | After 5 consecutive failures, all attempts are refused for 30 s (also applied to wrong-current-password on Change Password; typos in the *new* password do not count). The check-and-count is one **atomic** operation under a lock, so parallel requests cannot exceed the limit (tested with 60 simultaneous attempts → exactly 5 proceed). Constant-time comparison for both fields, both evaluated. A local process cannot lock the operator out because it cannot pass the launch gate to reach `/login` at all. Throttle is in-memory (cleared on restart) — accepted for a venue-local tool. |

Security headers on every response (including errors): `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, CSP `default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'` (no inline script/style anywhere — tested), `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`, `Permissions-Policy` (camera/mic/geolocation/payment/usb off).

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | Secret-scan test passes; nothing key-shaped in the tree. |
| F2 | Remaining secrets stored appropriately | N/A at this stage | Storage key from Phase 4 (DPAPI / Credential Manager decision, S-4). |
| F3 | Logs do not print secrets | **Pass** | The one-time launch token is never logged: the launcher logs the base URL only, and Werkzeug's per-request access log (which would print `/_launch?t=…`) is suppressed to WARNING. Verified in the real run: `ledsync.log` contains no token and no credentials. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G1 | Change/event log completeness | N/A at this stage | Web-app logs. |
| G2 | Failed/rejected actions logged without sensitive data | **Pass** | `operation_log` records Login Failed / Blocked / Success, Logout, Password Change (Success/Failed/Blocked), Application Startup. Failed-login rows carry no attempted username or password. Timestamps ISO-8601 UTC. |
| G3 | Monitoring plan | Pass | Operation log now; full log viewer in Phase 9; email in Phase 11. |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | No known-critical vulnerabilities in new dependencies | **Pass** | Phase 1 added **no new dependencies** (runtime closure unchanged from Phase 0; `pip-audit` clean on 20/09/26). |
| H2 | Supported runtime versions | Pass | Python 3.11.9 (security support to Oct 2027) — plan an upgrade before then. |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-3 | — | Loopback server had no authentication of its own. | **Closed in Phase 1** — launch gate + CSRF + SameSite=Strict cookie, all tested. |
| S-2 | Info | Accepted-risk plain-text admin credential. | Scope re-verified this release (B2). Re-check every release. |
| S-4 | Low | Storage key protection at rest (BRD 33.3). | Decide at Phase 4. |
| S-5 | Info | Default `admin` / `Admin@123` is documented in the BRD and remains the initial credential; the app does not force a change. | Owner decision (no forced change). Operators are advised to change it at first use. |
| S-6 | Medium → **Closed** | Architect review (demonstrated by replay): sessions were stateless signed cookies, so a cookie captured before logout or a password change still worked. | Fixed — server-side session epoch; tests TC-A35/A36. |
| S-7 | Low → **Closed** | Throttle check-then-count race under the threaded server. | Fixed — atomic `begin_attempt()`; test TC-A37. |
| S-8 | Low → **Closed** | `localhost` was on the Host allow-list (can resolve to `::1`); server advertised its versions; token was in the `RunningServer` repr. | Fixed; tests TC-A42/A43. |
| S-9 | Low | Two app instances can overwrite each other's session cookie (cookies are not port-scoped on 127.0.0.1); no single-instance guard yet. | Carried to Phase 6/12 (lock file / named mutex). |
| S-10 | Info | Password change is also compare-and-set now, ready for a second writer (headless run). | Done. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 20/09/26 | Approved with notes — no blockers; S-6/S-7/S-8 fixed |
| User (Vatsan) go-ahead | | | Pending |
