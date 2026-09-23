# Security Checklist — Desktop Phase 12: Scheduled Operation

Release/hand-off: Phase 12 — Scheduled Operation (Steps 12.1, 12.2). Date: 23/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (BRD-accepted items and any the owner explicitly signs off).

**Theme of this phase:** for the first time, this application stores and uses a real Windows account's credentials to run itself unattended, and introduces a second process (a headless scheduled run) that touches the same database and the same mapped LED folders as the interactive app. Three things must stay true: (1) the Windows account password is never stored, logged, or echoed anywhere by this application — Windows' own credential store is the only place it lives; (2) the interactive app and a scheduled run can never collide on the same data folder; (3) the headless entry point can never load any GUI toolkit, so it genuinely works with nobody signed in.

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed and protected | **Pass (unchanged)** | Not touched this phase. |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Pass (unchanged)** | `admin_*` keys untouched; `settings.py`'s scope guard still refuses any key it does not own. |
| B3 (added) | **The Windows account password is never stored by this application** | **Pass** | It exists only as a Flask request-form value for the single request that calls `scheduler.register()`, and as a Python local variable inside `register()`/`_invoke()` for the single `schtasks` call — never written to SQLite, never logged, never placed in a session, never returned in a response. `services/settings.py`'s `ScheduleSettings`/`save_schedule` do not have a password field at all - the password cannot reach that module even by mistake, since the view layer never passes it there. |
| B4 (added) | **A second, genuinely unattended execution path is behind the same access the interactive app already requires** | **Pass** | `Settings -> Scheduling` (GET/POST) requires `login_required` + CSRF, same as every other settings page. The headless run itself needs no HTTP login (it is not a web request at all - it is what Windows Task Scheduler launches directly), but it can only act on data the operator already configured while signed in to the app; it cannot change settings, cannot register itself, and cannot be reached over the loopback HTTP server (it never starts one). |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Local server transport | N/A at this stage | Unchanged; the headless run never starts an HTTP server at all - see B4. |
| C5 | Outbound connections and the read-only guarantee (BR 13) | **Pass** | Unchanged; the headless entry point calls the exact same, already-guarded `services/downloads.run_job` -> `services/storage.py` path the interactive app uses - no new Azure code, no new AST-guard surface. |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D1 | **The Windows account password never appears in any log, exception, or oplog message** | **Pass** | `scheduler.SchedulerError`'s message is always one of a small set of fixed sentences (see F below); `schtasks`' own stdout/stderr is captured but never interpolated into anything raised, logged, or shown. Verified: a failing registration's exception text and the operator-facing page error both exclude the password and schtasks' raw output, even though the fake test transport deliberately returns password-adjacent-looking text in `stderr` to prove this. |
| D2 | **The account name is not treated as a secret (correctly)** | **Pass** | A Windows account name is not sensitive on its own (the equivalent of a username) - shown back on the settings page, validated only for shape (and refused if it looks key-shaped, in case a password was pasted into the wrong box by mistake) like every other non-secret settings field. |
| D3 (added) | **The database and the real Task Scheduler entry can never silently disagree** | **Pass** | `scheduler.register()`/`unregister()` is called *before* `save_schedule()` persists anything; a failure refuses the whole save (the settings never claim "enabled" while Windows Task Scheduler has no matching task, or the reverse). |
| D4 (added) | **Cross-process safety: the interactive app and a scheduled run can never touch the database or a mapped LED folder at the same time** | **Pass** | `services/singleinstance.py`'s OS-level advisory lock (`msvcrt.locking`) is taken by both entry points before any database access; a second acquisition attempt fails immediately (never blocks, never silently proceeds), and the lock is released automatically by the operating system the instant the process exits - including a crash - so there is no stale-lock state requiring cleanup or manual recovery, unlike a PID file. |
| D5 (added) | **The headless entry point cannot load a GUI toolkit even by accident** | **Pass** | `scheduled_run.py` has no import of `webview`, Flask, or anything from `ledsync.web`, verified by a real subprocess test (mirroring the equivalent guarantee `ledsync.main` already had) - this matters because a GUI/COM toolkit can behave unpredictably or fail outright with no desktop session present (the "nobody logged in" case Addendum A 39.6 requires). |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | The Windows account field is validated (shape/length/secret-shaped-value checks); the Task Scheduler XML is built with every variable field (username, working directory, interpreter path) passed through `xml.sax.saxutils.escape` before insertion, so a crafted account name cannot break out of its XML element or inject a second trigger/action. `schtasks` is invoked via `subprocess.run` with an argument LIST (never a shell string), so there is no shell-injection surface regardless of what the account name or password contain. |
| E2 | Output encoding / XSS | **Pass** | Jinja auto-escaping for the new fields, matching every other settings field. |
| E5 | Errors do not leak internals | **Pass** | Every `scheduler.SchedulerError` is one of a small set of fixed, plain messages; `schtasks`' own output is captured but never shown to the operator or written to any log. |
| E12 (added) | **Background/unattended job safety** | **Pass** | `scheduled_run.py` catches per-event exceptions around each `downloads.run_job` call so one event's failure cannot stop the loop, and the outer `main()` catches everything else so a genuinely unexpected error still exits cleanly (logged to the diagnostic log) rather than leaving Task Scheduler to report a crash with no explanation in the application's own logs. |
| E15 (added) | **No shell injection via the generated Task Scheduler command** | **Pass** | The Task's `<Command>`/`<Arguments>` are the real Python interpreter path (`sys.executable`, not operator-controlled) and a fixed literal (`-m ledsync.scheduled_run`) - nothing about the schedule (days, time, account name) is concatenated into the command line the Task actually executes. |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | `test_no_secrets` passes; no Windows account or password exists in this repository, in a test fixture, or in a dev-environment fallback - there is deliberately no `.env`-style fallback for this credential at all, unlike the Storage Account key. |
| F3 | Logs do not print secrets | **Pass** | See D1. |
| F4 (added) | **The real storage location of the password is outside this application entirely** | **Pass (by design)** | Windows' own Task Scheduler / LSA credential store is the only place the password is retained after the single `schtasks /Create` call returns - this application has no code path that could read it back even if it wanted to. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G2 | Failed actions logged without sensitive data | **Pass** | Every `Scheduled Run` outcome (Started/Success/Failed/Blocked) is its own operational-log row with a fixed, non-sensitive message; a registration failure is logged via the existing `record_rejection` path (Invalid configuration), scrubbed the same way every other settings rejection already is. |
| G4 | Audit completeness and atomicity | **Pass** | The schedule's own settings save is one transaction with its audit row, unchanged from Phase 10's pattern; the Task Scheduler registration happens as a separate, necessarily-non-transactional real-world action *before* that save, by design (D3). |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | New dependency reviewed | **Pass** | None. `schtasks.exe` is a built-in Windows component (no package to audit); `msvcrt` is Python's own standard library (Windows-only, matching this application's Windows-only scope). `pip-audit` output is unchanged from Phase 11. |
| H2 | Supported runtime | Pass | Python 3.11.9, Windows 11 Pro (Task Scheduler and `msvcrt` are both Windows-specific, consistent with the whole application). |

## Findings

*Independent Solution Architect review pending - findings, if any, will be recorded here.*

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | pending | pending |
| User (Vatsan) go-ahead | Vatsan | pending | pending - also needs a real Task Scheduler registration and a real unattended run, performed by the owner directly in the app (a Windows account password must never be typed into this conversation) |
