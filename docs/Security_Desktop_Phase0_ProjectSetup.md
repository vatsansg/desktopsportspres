# Security Checklist — Desktop Phase 0: Project Setup

Release/hand-off: Phase 0 — Project Setup. Date: 20/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (only the two BRD-accepted items).

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | All web-application items (Managed Identity, MySQL, RBAC, Export Event, web default password) | N/A at this stage | Web application only. This desktop app has no Azure-hosted identity and no user roles. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed and protected in local configuration | **Pass** | No key is used by the app yet. `.gitignore` (created before the first commit) excludes `.env`, `.env.*`, `OBSERVATIONS.txt` and credential file patterns; `.env.example` holds blank placeholders only. `tests/test_no_secrets.py` scans everything `git add .` could stage and was proven to fail on a planted key-shaped string. **See finding S-1 below — a live key exists in plaintext in the parent repository, outside this project.** |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **N/A at this stage** | Login is Phase 1. Grep-verified that no Phase 0 code, test or config contains the `admin` / `Admin@123` credential (it appears only in the BRD and in this note). Will be re-checked in Phase 1. |
| B3 | Shared-folder credentials not logged in plaintext | **N/A at this stage** | No shared-folder or credential handling exists yet (Phase 5/8). Logging in Phase 0 is stderr only and logs paths/URLs, no credentials. |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | HTTPS-only App Service, MySQL TLS, MySQL network rules, App Service CORS | N/A at this stage | Web-app items. Desktop equivalent: the local UI server binds `127.0.0.1` only on a random port (verified by test) and rejects any request whose `Host` header is not `127.0.0.1:<port>` / `localhost:<port>` (DNS-rebinding defence; fails closed — refuses everything until hosts are configured — verified by test). It is plain HTTP on loopback by design; nothing listens on the network. No CORS headers are emitted. |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D1, D2 | Storage containers not public; encryption at rest | N/A at this stage | No Azure access yet. |
| D3, D4 | Server-side validation of duration / uploaded files | N/A at this stage | Web-app uploads. This app never uploads. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **N/A at this stage** | The only endpoint is `GET /`; no user input is accepted. SQLite schema uses no dynamic SQL from input. Must be re-verified from Phase 1 (login) onward — parameterised queries only. |
| E2 | Upload size limits | N/A at this stage | Web-app only. |
| E3 | Path traversal via filenames | N/A at this stage | Critical from Phase 7: change-log paths from Azure are untrusted input when building local paths. Flag now so it is designed in. |
| E4 | Session/authentication tokens | N/A at this stage | No sessions until Phase 1. Groundwork done: a random per-launch `SECRET_KEY` (never persisted or hard-coded) is generated. **Phase 1 must add** a per-launch access token exchanged for an HttpOnly, SameSite=Strict cookie, plus CSRF protection, before any POST endpoint exists — a random loopback port alone is obscurity, not authentication (finding S-3). |
| E5 | Error messages do not leak internal details | **Pass** | The web layer returns only default 404/400 bodies. Startup failures are shown to the local operator in a dialog (message text only) with the stack trace written to `logs\ledsync.log`, never to the web UI. Exception text in that dialog could include a local path; acceptable for a local admin dialog, revisit in Phase 9. |

Security headers verified on every response: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, CSP `default-src 'self'` (no inline script/style).

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | Nothing committed yet (no commit until user go-ahead). Working tree scanned by test, clean. |
| F2 | Remaining secrets stored as settings, not hard-coded | **N/A at this stage** | No ACS/email key until Phase 11; storage key from Phase 4 goes only in the local `.env`. Note (BRD 33.3): plain `.env` is the minimum; consider Windows DPAPI protection at Phase 4 — decision to raise then. |
| F3 | Logs do not print secrets | **Pass** | Startup logs the data-directory path and loopback URL only. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G1 | Change log / event log completeness | N/A at this stage | Web-app logs; desktop logs begin Phase 6/9. |
| G2 | Failed/rejected actions logged | N/A at this stage | `operation_log` / `exception_log` tables exist (empty); population begins Phase 3/9. |
| G3 | Monitoring plan | **Pass** | Desktop app on a venue machine: monitoring = operation/exception logs (Phase 9) and the IT notification email (Phase 11). |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | No known-critical vulnerabilities in new dependencies | **Pass** | `pip-audit` on the full pinned runtime closure (15 packages: Flask 3.1.3, Werkzeug 3.1.8, pywebview 6.2.1, pythonnet 3.1.0, etc.): *No known vulnerabilities found* (20/09/26). Dev-only deps (pytest) not shipped. |
| H2 | Runtime versions supported | **Pass** | Python 3.11.9 (security support to Oct 2027). Note: 3.11 reaches end of life within the lifetime of this product — plan an upgrade. |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| **S-1** | **High (outside this repo)** | The live Storage Account access key exists in plaintext in the *parent* repository at `references/0forimplementation/desktop/OBSERVATIONS.txt`. It is untracked but **not** git-ignored there, and that repo has a GitHub remote (`ovrledassetmanagement`), so a `git add .` in the parent would publish it. History was checked: it has never been committed. This contradicts Solution Plan Section 1.1 ("never written into any file under ledassetmanagement"). | **Resolved 20/09/26:** owner instructed the file be git-ignored; done in the parent `.gitignore` and verified with `git check-ignore`. File contents untouched. Key rotation: owner's decision, still open. |
| S-2 | Low | Accepted-risk plain-text admin credential: no scope expansion in Phase 0 (nothing implemented). | Re-check Phase 1. |
| S-3 | Medium | Loopback UI server has no authentication of its own: any local process or a hostile web page in a browser on the venue machine could reach `127.0.0.1:<port>` if it guessed the port. Impact is nil in Phase 0 (one static page) but rises sharply once login/settings endpoints exist. | Phase 1: per-launch token + HttpOnly SameSite=Strict cookie + CSRF protection, before any POST endpoint. |
| S-4 | Low | Storage key protection at rest (BRD 33.3): `.env` / env var is a development convenience only. In production the key is entered in Settings and must be stored protected (Windows DPAPI or Credential Manager), not in `application_settings` as plain text. | Decide at Phase 4 together with the data-directory/account decision (user vs machine scope) needed for Phase 12. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 20/09/26 | Approved with notes |
| User (Vatsan) go-ahead | | | Pending |
