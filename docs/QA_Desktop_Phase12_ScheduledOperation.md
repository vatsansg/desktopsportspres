# QA Test Case Document — Desktop Phase 12: Scheduled Operation

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 12 — Scheduled Operation (Step 12.1 scheduling configuration — storage already built in Phase 10; Step 12.2 Windows Task Scheduler integration) |
| BRD reference(s) | Desktop BRD Section 22 (Scheduled Download and Synchronisation), Section 14 (Scheduling Settings), Section 31 (Scheduled Workflow), Section 27 (Installation Requirements — Task Scheduler); Desktop BRD Addendum A Section 39.6 ("whether scheduled runs must work with no user logged in") |
| Implementation Sequence reference | Steps 12.1, 12.2 |
| Date | 23/09/26 |
| Tested by | Claude Code (automated only — real Task Scheduler registration with a real Windows account password needs the owner; see "Live validation" below) |
| Environment | Windows 11 Pro, Python 3.11.9; a fake `schtasks` runner (dependency-injected) for every automated test — no automated test ever touches the real Windows Task Scheduler. |

## Owner decisions applied (23 September 2026) — confirmed before any code was written

- **A scheduled run must work with nobody logged into Windows** (Addendum A 39.6, previously open, resolved by the owner). This is the single biggest design decision of the phase: it requires a genuinely headless entry point with no GUI dependency at all, and Windows Task Scheduler running the task under `LogonType=Password` (a real stored account/password), not the simpler "only when logged on" model.
- **A scheduled run processes every registered event**, one after another — not a per-event opt-in/opt-out.
- **Settings → Scheduling registers the real Windows Task Scheduler entry itself**, the moment the schedule is saved (via `schtasks.exe`), rather than waiting for the Phase 13 installer to do it — Phase 13 can take this over later without disturbing an already-configured schedule.
- **A missed occurrence (computer off/asleep) catches up as soon as the computer is next on** — Task Scheduler's own `StartWhenAvailable` setting.

## What was built

- **`services/singleinstance.py`** (closes the "single-instance lock" item carried forward since Phase 0/1, and BRD's own "unattended runs in Phase 12" note from Phase 5's F-32): an OS-level advisory file lock (`msvcrt.locking`), held for the life of the process and released automatically by Windows even on a crash — no stale-lock state possible. Both the interactive app (`main.py`) and the headless scheduled run take this same lock before touching the database, so at most one of the two ever runs at a time for a given data folder.
- **`services/scheduler.py`**: builds a Task Scheduler XML definition (weekly `CalendarTrigger` on the selected days/time — this computer's own local clock, matching how Task Scheduler itself works; `LogonType=Password`; `StartWhenAvailable=true`; `MultipleInstancesPolicy=IgnoreNew`) and registers/removes/queries it via `schtasks.exe`. The Windows account password is used only to pass to `schtasks /RP` at registration time — **never stored by this application anywhere**; Windows' own credential store (LSA secrets) retains it, exactly as if an administrator had typed it into the Task Scheduler UI by hand. `schtasks`' own stdout/stderr is never echoed into any log or message.
- **`ledsync/scheduled_run.py`** (the headless entry point Windows Task Scheduler invokes as `<python> -m ledsync.scheduled_run`): imports nothing from pywebview or the Flask web layer — confirmed by a dedicated test that a subprocess importing this module never loads `webview` into `sys.modules`, mirroring the same guarantee `main.py` already gave `ledsync.main`. Runs the exact same Download & Sync + notification pipeline the interactive Dashboard's own button uses (`services/downloads.run_job`, completely unchanged) for every registered event, one after another; one event's failure is caught and logged but never stops the rest. Brackets the whole multi-event run with its own `Scheduled Run` Started/Success/Failed/Blocked operational-log rows (BRD Section 25's operation vocabulary already reserved this name in Phase 9).
- **`services/settings.py`**: a new `schedule_username` field (not sensitive — just which Windows account the Task runs as, shown back on the page) alongside the existing enabled/days/time (Phase 10). The password is deliberately **not** a field here at all — it never reaches this module, or the database, in any form.
- **Settings → Scheduling page**: a Windows account field (auto-filled with the signed-in operator's own account) and a password field (required every time the schedule is saved while enabled — there is nothing to "keep unchanged" the way the ACS connection string works, since the password is never stored). A "Registered" / "Not registered" badge reflects the real, current Task Scheduler state (`schtasks /Query`), not just what this application's own database says.
- **Ordering that keeps the database and the real Task Scheduler entry from ever disagreeing**: `scheduler.register()`/`unregister()` is called *before* the settings are persisted. A bad account or password refuses the whole save — exactly like every other validation failure in this application — rather than leaving the saved settings claiming "enabled" while Windows disagrees.
- 19 new tests (`tests/test_phase12_scheduled.py`), plus a project-wide safety-net test fixture (`conftest.py`) that guarantees no test anywhere can shell out to the real `schtasks.exe` by accident.

## Live validation — NOT YET DONE (needs the owner, and deliberately does NOT ask for a password in chat)

Unlike the Storage Account key (Phase 4) or the Azure Communication Services connection string (Phase 11), **a Windows account password must never be typed into this conversation, or into any file, script, or chat message** — it is real, standing access to the operator's own Windows account and the venue network. The only safe place to type it is directly into the running application's own Settings → Scheduling page.

**To validate for real, on a real machine:**
1. Open Settings → Scheduling in the real app.
2. Tick a day close to "now", set a time a few minutes ahead, confirm the Windows account shown is correct, and type that account's real password.
3. Save. The page should show "Registered" and a green confirmation.
4. Open Windows Task Scheduler (`taskschd.msc`) and confirm a task named **LEDAssetSync Scheduled Run** exists, is enabled, and its Triggers/Settings match what was configured (in particular: **General** tab shows "Run whether user is logged on or not").
5. Wait for the scheduled time (or right-click the task in Task Scheduler and choose **Run**) and confirm: the Dashboard shows the event(s) updated afterwards, and the Logs page shows a `Scheduled Run` row bracketing the per-event `Download & Sync` rows.
6. **The real "works with nobody logged in" guarantee**: sign out of Windows entirely (not just lock the screen) before the scheduled time, then sign back in afterwards and confirm the run still happened.
7. Turn scheduling off and confirm the Task Scheduler entry is removed.

## Test cases — automated

| ID | Description | Status |
|---|---|---|
| TC-A01 | **Single-instance lock:** a second acquisition attempt (a different file handle, simulating a second process) fails immediately, never blocks; the lock releases cleanly when the `with` block exits and can be re-acquired; the lock file is created automatically if missing | Pass |
| TC-A02 | **Task Scheduler XML:** the right `DaysOfWeek` elements for the selected days only, the right start time, `StartWhenAvailable=true`, `LogonType=Password`, the real Python interpreter path and `-m ledsync.scheduled_run`, and the username is XML-escaped (a `&`/`<`/`>` in the account name cannot corrupt the document) | Pass |
| TC-A03 | **`register()`:** calls `schtasks /Create` with the task name, the generated XML file, `/RU`/`/RP` with exactly the given account/password, `/F`; the temporary XML file is deleted afterwards even on success | Pass |
| TC-A04 | **A failed registration** (schtasks returns non-zero) raises `SchedulerError` with a fixed, generic message — the account name, password and schtasks' own output never appear in it | Pass |
| TC-A05 | **`unregister()`** calls `schtasks /Delete` and never raises even when schtasks reports nothing to delete | Pass |
| TC-A06 | **`is_registered()`** reflects `schtasks /Query`'s return code | Pass |
| TC-A07 | **Windows account setting:** validated (key-shaped values refused, same guard as every other settings field), required together with days/time when enabling; persists and round-trips | Pass |
| TC-A08 | **The Settings → Scheduling route, real form flow (fake `schtasks` throughout):** enabling calls `scheduler.register()` with the typed account/password and saves; missing password refuses the save *before* `scheduler.register()` is ever called; a `SchedulerError` from registration refuses the whole save and leaves `schedule_enabled` untouched in the database; disabling calls `scheduler.unregister()`; the page's "Registered"/"Not registered" badge reflects the real (faked) `schtasks /Query` result live, not a cached assumption | Pass |
| TC-A09 | **The headless run never imports pywebview** — confirmed the same way `ledsync.main` already is, via a real subprocess import check | Pass |
| TC-A10 | **No registered events:** logs `Scheduled Run` Started then Success ("No events are registered; nothing to do"), touches nothing else | Pass |
| TC-A11 | **Cloud Storage not configured:** logs `Scheduled Run` Failed with a plain message, and `downloads.run_job` is never called for any event | Pass |
| TC-A12 | **One event failing never stops the others:** two registered events, the first's `run_job` call raises; both events are still attempted, and the wrapping `Scheduled Run` row still reports Success (the per-event failure is its own concern, already logged by `run_job` itself, per Phase 8/9/11's existing behaviour) | Pass |
| TC-A13 | **Lock contention:** if another instance (interactive or a previous scheduled run) already holds the lock, the run logs `Scheduled Run` / `Blocked` and exits cleanly without touching anything else | Pass |

`.\.venv\Scripts\python -m pytest -q` → **1489 passed, 14 skipped** (the skipped are the live-Azure-Storage tests from earlier phases; there is no live-Task-Scheduler test, for the reason given above).

## Rendered-page check

| ID | Description | Result |
|---|---|---|
| TC-R01 | Settings → Scheduling: updated lede text (works with nobody logged in; catches up on a miss), the new Windows account/password fields, the "Registered"/"Not registered" badge, the auto-filled current Windows account (`getpass.getuser()`). One orange primary action; bordered inputs; consistent with the rest of Settings | Pass |

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-77 | No live validation against the real Windows Task Scheduler yet — deliberately not attempted here, since it needs the operator's own Windows account password, which must never be typed anywhere but the application's own Settings page. See "Live validation" above. | Info | **Needs the owner, in the real app, before go-ahead** |
| F-78 | Windows shared-folder authentication (BRD §36, carried since Phase 5) is still "the Windows account the application runs under" — for a scheduled run that account is now whatever was configured in Settings → Scheduling, which must itself have the same LED-folder access an interactively signed-in operator has. Not a new gap, but worth the owner double-checking for the account chosen. | Info | Carried, accepted |
| F-79 | `ExecutionTimeLimit` on the generated Task is fixed at 2 hours. A very large multi-event run (many gigabytes, many events) could in principle exceed this, at which point Task Scheduler would terminate it. No real event has approached this in any live validation so far (Phase 7/8's real Event 1000: ~100s). | Low | Accepted, tell me if wrong |
| F-80 | The scheduled time is this computer's own local clock (matching how Windows Task Scheduler itself works), not UTC — deliberately different from the per-event Timestamp Cut-off (Phase 10), which is UTC because it compares against cloud timestamps. Worth the owner confirming this reads naturally. | Info | Accepted, tell me if wrong |

## Independent Solution Architect review

*Pending.*

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | pending | pending |
| User (Vatsan) go-ahead | Vatsan | pending | pending — also needs a real Task Scheduler registration and a real unattended run, done by the owner directly in the app (see "Live validation") |
