# LED Asset Download & Sync — User Guide

For anyone operating this application at a venue: registering events, running downloads and
synchronisation, and configuring Settings. For installing or upgrading the application itself, see
`DEPLOYMENT_GUIDE.md` in this same folder instead.

## Contents

1. What this application does
2. Signing in
3. The Dashboard
4. Registering an event
5. An event's own page: LED configuration, device mapping, the timestamp cut-off
6. Running a Download & Sync
7. Logs: operational history and exceptions
8. Settings
   - Cloud Storage
   - Local Folders
   - Download (retry behaviour)
   - Scheduling
   - Email Notification
   - Application (data location and log retention)
9. Email notifications
10. Scheduled (unattended) runs
11. Troubleshooting

---

## 1. What this application does

For each registered event, it downloads the sports-presentation assets from the event's Azure
Storage location and copies them to the shared folders your LED display tables read from — read
only against Azure; it never writes anything back there. It keeps a record of every file it moves,
every device it talked to, and anything that went wrong, and can run this automatically on a
schedule or email someone a summary when it finishes.

## 2. Signing in

Open the application from its shortcut. Sign in with the administrator account (your IT team sets
this up — the software ships with a default of `admin` / `Admin@123`, which should be changed
immediately on a real venue machine).

**To change the password**: top-right of any page → **Change Password** → enter your current
password and a new one (at least 8 characters) twice.

**To sign out**: top-right → **Sign Out**.

Five failed sign-in attempts locks login out for 30 seconds — this is normal, just wait and try
again.

## 3. The Dashboard

The page you land on after signing in. Shows every registered event in a table:

| Column | Meaning |
|---|---|
| Event ID / Name | As registered from Azure |
| Status | Where the event currently stands (see below) |
| Last Download / Last Sync | When each last ran, and how it went |
| Actions | **Download & Sync** and **Test Connections** buttons for that event |

**Download & Sync** runs the full pipeline for that one event: checks what's changed, downloads
anything new, and pushes it to the LED devices. **Test Connections** checks that every mapped LED
device is reachable without downloading or sending anything — useful before a show, or after
network changes at the venue.

**ADD NEW EVENT** (top of the Events panel) starts registering a new event — see the next section.

**Operation Status** panel: shows a run in progress (with a Cancel button) and, once it finishes,
what happened — files identified, downloaded, synchronised, and any errors, exactly matching what
gets logged and what an email notification (if enabled) reports.

## 4. Registering an event

**Dashboard → ADD NEW EVENT.** Enter the **Event ID** exactly as it appears in the web application
(for example `1000`) and click **REGISTER EVENT**. The application finds that event's folder in
Azure Storage on its own and downloads its configuration — there's nothing else to type or upload
by hand. If the Event ID doesn't match anything in Azure, or the Cloud Storage settings aren't
configured yet, you'll see a clear message telling you which.

Re-registering an event you've already registered is safe: if nothing about the event's identity
has changed, it's a no-op. If the event's underlying configuration genuinely changed (a real
re-export from the web application), you'll be told so and asked to confirm before anything is
overwritten.

## 5. An event's own page

Click an event's name from the Dashboard to open it.

**LED Configuration**: a read-only summary of the tables/LED types this event defines, straight
from what was downloaded at registration.

**Device Mapping**: for each Table/LED-type combination, enter the destination — an IP address or
a shared folder path — and whether it's enabled. **Test All Connections** (or the per-row test
button) checks reachability without moving any files. **SAVE MAPPING** saves your changes. A
disabled row is skipped entirely by Download & Sync and Test Connections.

**Timestamp Cut-off**: an optional daily quiet period — tick the box and enter a time (**in UTC**,
not this computer's local time) to hold back any file changed after that time each day, so a
last-minute edit close to a show doesn't get pushed live mid-event. The page shows you, in plain
language and in this computer's own local time, exactly when the current cut-off boundary falls.
Leave it unticked to always download the very latest version of everything.

## 6. Running a Download & Sync

From the Dashboard (one event) or automatically on a schedule (every registered event — see
section 10). Each run:

1. **Checks changes** — compares the event's change log against what's already been downloaded.
2. **Downloads** anything new or changed from Azure (skipping anything held back by that event's
   Timestamp Cut-off).
3. **Synchronises** — pushes the downloaded files to every enabled, mapped LED device.

One file or device failing never stops the rest — every other file/device in the same run still
gets attempted, and the failure is recorded in the Exception Log (see the next section) alongside
the run's own overall status on the Dashboard.

## 7. Logs

Top-right → **View Logs**. Two tabs:

- **Operational Log**: every significant thing the application did (a run starting/finishing, a
  setting being changed, a login, an email sent, and so on).
- **Exceptions**: every error or failure, with enough detail to diagnose it (which file, which
  table/LED type, which event). Each row has its own **Resolution** status you can update (e.g.
  mark something reviewed) — this doesn't change what happened, just tracks whether someone's
  looked at it.

Both tabs share the same filters across the top: event, operation/category, status, a date range,
and free-text search — **APPLY FILTERS** to narrow the list. Use the export button to save the
currently filtered view as a CSV file.

## 8. Settings

Top-right → **Settings**. Six sections along the top of every Settings page.

### Cloud Storage

The Azure Storage account, container, and access key this application reads from. **Test
Connection** confirms the credentials actually work before you rely on them. The access key is
never shown back once saved — only whether one is currently set.

### Local Folders

Where downloaded files land on this computer: the **Asset folder** (Table/LED-type files) and the
**RPI folder**. Leave either blank to use the built-in default location. The two folders must be
different, and neither can be inside the other or inside the application's own data folder.

### Download

How a download or a push to a device retries after a transient failure (a dropped connection, a
device that was briefly unreachable) — how many extra tries, and how long to pause between them.
The same setting governs both directions.

### Scheduling

Runs a Download & Sync for every registered event automatically, on selected days and a time of
day (this computer's own local clock). Enter the Windows account it should run as and — every time
you save while enabled — that account's real password (never stored; typed fresh each time). The
page shows **Registered** or **Not registered**, reflecting the real state of Windows Task
Scheduler, not just what's saved here. See `DEPLOYMENT_GUIDE.md` for the one-time Windows setup a
scheduled run needs before it will actually work (the "Log on as a batch job" right, and file
access if the scheduled account differs from the one you use day to day).

### Email Notification

Sends a summary email after every full Download & Sync (interactive or scheduled) — files
identified/downloaded/synchronised and the overall outcome. Enter the notification recipient(s)
(comma-separated for more than one), the sender address, and the Azure Communication Services
connection string, then turn it on. If the venue has no internet connection at the moment a run
finishes, the notification is silently skipped rather than failing the run.

### Application

Where this computer keeps its own database and diagnostic log (fixed, shown for reference), and
how many days of operational/exception log history to keep — leave blank to keep it forever.

## 9. Email notifications

Sent automatically once a full Download & Sync finishes (never for a download-only or sync-only
partial run), if enabled in Settings → Email Notification. The subject line names the event and
its outcome; the body has the same counts (identified/downloaded/synchronised/failed) shown in the
Dashboard's Operation Status panel. Every attempt — sent, skipped, or failed — is recorded in the
Operational Log.

## 10. Scheduled (unattended) runs

Once enabled in Settings → Scheduling, this runs completely unattended — with nobody signed into
Windows, if the account it runs as is set up correctly (see `DEPLOYMENT_GUIDE.md`). It runs a
Download & Sync for **every** registered event, one after another; a missed occurrence (computer
off or asleep) catches up as soon as the computer is next on. A scheduled run and someone using the
application interactively at the same time can never collide — whichever started first finishes
first, and the other is safely skipped and logged as such, not silently queued or dropped.

## 11. Troubleshooting

- **Something failed** — check the **Exceptions** tab of Logs first; it names the event, file, and
  device involved.
- **The application won't start at all**, or you see an error dialog on launch — a diagnostic log
  is written to `logs\ledsync.log` inside the application's data folder (Settings → Application
  shows the exact path); include this if you need support.
- **A scheduled run never seems to happen** — check Settings → Scheduling shows **Registered**; if
  not, see `DEPLOYMENT_GUIDE.md`'s Scheduling prerequisites section.
- **An email notification never arrives** — check the Operational Log for an `Email Notification`
  row; it will say Sent, Skipped (no internet at the time), or Failed with a reason.
