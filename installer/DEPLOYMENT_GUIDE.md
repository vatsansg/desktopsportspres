# LED Asset Download & Sync — IT Deployment Guide

For the IT operations team installing this application on a venue machine. Not a build guide —
see `installer/README.md` for that (only needed by whoever produces a new installer build).

## What you need

Download these two files from the `desktopinstaller` container
(`https://sasportspresentation.blob.core.windows.net/desktopinstaller`):

| File | Required? |
|---|---|
| `LEDAssetSync-Setup-<version>.exe` | Yes — the installer |
| `install-config.example.json` | Optional — a template for pre-filling common settings on a fresh install (see below) |

No code signing certificate, no separate runtime installer — everything the application needs is
bundled into the one `.exe`. Windows 11 has the Microsoft Edge WebView2 Runtime pre-installed; if
you are deploying to an older Windows 10 machine that somehow lacks it, the application will tell
you exactly that with a link, the first time it's launched.

## Install (interactive — the normal way)

1. Copy `LEDAssetSync-Setup-<version>.exe` to the venue machine.
2. **If you're using the pre-install config file** (see below), place your edited
   `install-config.json` in the **same folder** as the `.exe`, before running it.
3. Double-click the `.exe`. No administrator rights are needed — it installs for the current
   Windows account only, under `%LocalAppData%\Programs\LEDAssetSync`.
4. Follow the wizard (choose whether to also create a desktop shortcut, then Install, then
   Finish). It does not launch the application automatically when it finishes.
5. Launch it from the Start Menu shortcut it created.

## Install (silent — for scripted/unattended rollout)

```powershell
.\LEDAssetSync-Setup-0.1.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

No window, no prompts. If an `install-config.json` sits next to the `.exe`, it's applied
automatically on a fresh install (silently skipped — never overwriting anything — on an upgrade).

## The pre-install configuration file

Saves retyping the same Cloud Storage / Email Notification / default Scheduling values into
Settings on every venue machine. Copy `install-config.example.json` to `install-config.json`,
fill in the real values, and place it next to `Setup.exe` before installing (not inside this
repository — keep it with the installer file itself, e.g. on the same USB drive or network
share).

- Every field is optional — leave out anything you don't want pre-filled.
- **Never put a Windows account password in this file** — there is no field for one. Scheduling
  still needs the password typed once, directly into Settings → Scheduling, after install (see
  below).
- Only applies to a genuinely fresh install — an upgrade never touches an already-configured
  database.
- Delete or secure this file once your rollout is done — like any file holding a real storage key
  or connection string, treat it as sensitive while it exists.

## After installing — validation checklist

1. **Sign in.** Default administrator: `admin` / `Admin@123`. Change this password immediately
   (Change Password, top right) if this is a real venue machine.
2. **Cloud Storage** (Settings → Cloud Storage): confirm the account/container are correct (either
   pre-filled from your config file, or enter them now), enter the access key if not already set,
   and click **Test Connection**. This should succeed and confirms the installed application can
   really reach Azure Storage over the network — the single most important post-install check.
3. **Register the real event** for this venue (or a test event) and run a **Download & Sync** —
   confirms the full real pipeline works end to end from this specific machine.
4. **Email Notification** (Settings → Email Notification), if this venue uses it: confirm
   sender/recipient/connection string, and check a completion email actually arrives after the
   Download & Sync above.
5. **Scheduling** (Settings → Scheduling), if this venue uses it: see the next section first —
   there are two one-time Windows prerequisites — then enter the account and password and enable
   it. Confirm the page shows **"Registered."**

## Before enabling Scheduling — two one-time Windows prerequisites

These are standard Windows security requirements, not something the installer or the application
can set up for you:

- **The Windows account needs the "Log on as a batch job" right.** Without it, the scheduled run
  will fail to launch at all. Grant it via `secpol.msc` → Local Policies → User Rights Assignment →
  "Log on as a batch job" → add the account. On a domain/Intune-managed machine this may need a
  Group Policy change instead of a local one.
- **If the scheduled account is different from the account you're using interactively**, it needs
  explicit file access to `%LocalAppData%\LEDAssetSync` (that account's own version of that path).
  Simplest fix: use the same account for both. Otherwise, grant access explicitly:
  `icacls "%LocalAppData%\LEDAssetSync" /grant <account>:(OI)(CI)F`.

## Upgrading to a newer version

Download the newer `LEDAssetSync-Setup-<version>.exe` and run it the same way (interactive or
silent) — it installs over the existing application files only. The database, registered events,
mappings, operational history, and all Settings are preserved automatically; nothing needs to be
backed up first. A pre-install config file, if one happens to be present, is never applied on an
upgrade.

## Uninstalling

**Settings → Apps → LED Asset Download & Sync → Uninstall** (or the Start Menu's own "Uninstall"
shortcut). This removes the application and its shortcuts, and also removes any Windows Task
Scheduler entry Scheduling created. It does **not** remove `%LocalAppData%\LEDAssetSync` (the
database, downloaded assets, and logs) — delete that folder by hand only if you genuinely want to
wipe all history for this machine.
