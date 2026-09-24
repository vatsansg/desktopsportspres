"""Pre-install configuration seeding (Phase 13; owner request, 23 Sep 2026).

An operator preparing several venue machines can edit ONE JSON file once, so the account-level
defaults that are the same across every event - Cloud Storage, Email Notification, and a default
Scheduling day/time/account - do not need to be retyped into Settings after every fresh install.

Applies ONLY to a brand-new database (see `should_seed`) - never to an upgrade. This is not just the
installer's own fresh-vs-upgrade decision: `seed()` independently refuses to run against a database
that already has ANY saved setting, so a mistaken re-run (or a config file left behind and picked up
by a later upgrade) can never silently overwrite a venue's already-configured installation - BRD
13.2's "preserve the existing database" guarantee holds here exactly as everywhere else.

Every field is validated and saved through the SAME `save_*`/`validate_*` functions the Settings
pages already use (imported from `settings.py`, never reimplemented here) - a malformed value in
the file is refused exactly as if it had been typed into the UI, never partially applied.

The Windows Scheduling PASSWORD is never accepted here, in any form - Phase 12 established that this
application never stores that password anywhere, and a pre-install config file is no exception. The
config file may set the Scheduling ACCOUNT NAME (not sensitive - the equivalent of a username) so the
Settings -> Scheduling page is pre-filled, but the operator still types the real password once,
directly into that page, after installing.
"""

import json
import sqlite3
from pathlib import Path

from . import settings as cs

# Fields this module will read from the config file. schedule_password is deliberately NOT here -
# see the module docstring - and is checked for explicitly in seed() so a file containing it by
# mistake is refused with a clear message rather than silently ignored.
_CLOUD_FIELDS = ("cloud_storage_account", "cloud_container", "storage_access_key")
_EMAIL_FIELDS = ("email_enabled", "email_recipient", "email_sender", "email_connection_string")
# schedule_enabled is deliberately NOT a field here (see seed()): unlike email, "enabled" for
# scheduling means a REAL Windows Task Scheduler entry exists (services/scheduler.register()),
# which needs a password this file must never contain - so seeding can only ever pre-fill the
# day/time/account DEFAULTS, never actually turn scheduling on.
_SCHEDULE_FIELDS = ("schedule_days", "schedule_time", "schedule_username")
_RETRY_FIELDS = ("download_retry_count", "download_retry_delay")
_FOLDER_FIELDS = ("rpi_folder", "asset_folder")
_KNOWN_FIELDS = frozenset(
    _CLOUD_FIELDS + _EMAIL_FIELDS + _SCHEDULE_FIELDS + _RETRY_FIELDS + _FOLDER_FIELDS + ("log_retention_days",)
)


class InstallConfigError(ValueError):
    """A rejected install-config file or field; the message is always safe to show or log - it
    never echoes a secret-shaped value back (the same discipline `settings.py` already applies)."""


def should_seed(conn: sqlite3.Connection) -> bool:
    """True only for a database with no saved settings at all - the definition of "brand new" this
    module uses, deliberately independent of whatever fresh-vs-upgrade decision the installer itself
    already made. Delegates to settings.py (rather than querying the settings table here directly),
    which is the only module allowed to touch it - see its SCOPE GUARD note."""
    return not cs.has_any_setting(conn)


def load_config_file(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise InstallConfigError(f"Could not read the install configuration file: {path}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InstallConfigError(f"The install configuration file is not valid JSON ({exc}).") from None
    if not isinstance(data, dict):
        raise InstallConfigError("The install configuration file must contain a single JSON object.")
    if "schedule_password" in data:
        raise InstallConfigError(
            "The install configuration file must not include 'schedule_password' - a Windows account "
            "password is never stored by this application. Remove it and enter the password directly "
            "in Settings -> Scheduling after installing.")
    if "schedule_enabled" in data:
        raise InstallConfigError(
            "The install configuration file must not include 'schedule_enabled' - turning scheduling on "
            "registers a real Windows Task Scheduler entry, which needs a password this file must never "
            "contain. Set 'schedule_days'/'schedule_time'/'schedule_username' to pre-fill the Settings -> "
            "Scheduling page instead, then enable it (and enter the password) there after installing.")
    if "email_enabled" in data and not isinstance(data["email_enabled"], bool):
        # Found by the Phase 13 pre-hand-off review: Python's bool("false") is True, so a
        # hand-edit mistake like "email_enabled": "false" (a quoted string - plausible, since
        # most fields in this file ARE quoted strings) would otherwise silently turn notifications
        # ON. A real JSON boolean (true/false, no quotes) is required explicitly.
        raise InstallConfigError(
            "'email_enabled' must be a real JSON boolean (true or false, not a quoted string).")
    # A leading underscore is a documentation-only convention (JSON has no real comments) - e.g.
    # "_comment" in the example file - never a real setting, so it is exempt from the unknown-field
    # check below and simply ignored by seed().
    real_fields = {k for k in data if not k.startswith("_")}
    unknown = sorted(real_fields - _KNOWN_FIELDS)
    if unknown:
        raise InstallConfigError(f"Unrecognised field(s) in the install configuration file: {', '.join(unknown)}.")
    return data


def seed(conn: sqlite3.Connection, data: dict, data_dir) -> list[str]:
    """Validate and save every group of fields present in `data`. Returns the names of the setting
    groups actually saved. Raises InstallConfigError (never touching anything) if the database is
    not brand-new, or if any field fails the exact same validation the Settings pages already
    enforce (the underlying SettingsError is wrapped so callers only need to catch one exception
    type here)."""
    if not should_seed(conn):
        raise InstallConfigError(
            "This database already has saved settings; the install configuration file was not applied - "
            "it only ever applies to a brand-new installation, never an upgrade.")
    changed: list[str] = []
    try:
        if any(k in data for k in _CLOUD_FIELDS):
            cs.save_cloud(conn, data.get("cloud_storage_account", ""), data.get("cloud_container", ""),
                         data.get("storage_access_key") or None)
            changed.append("cloud storage")
        if any(k in data for k in _EMAIL_FIELDS):
            cs.save_email(conn, bool(data.get("email_enabled", False)), data.get("email_recipient", ""),
                         data.get("email_sender", ""), data.get("email_connection_string") or "")
            changed.append("email notification")
        if any(k in data for k in _SCHEDULE_FIELDS):
            # enabled is always False here - see the module/field-list comments above.
            cs.save_schedule(conn, False, data.get("schedule_days", []),
                             data.get("schedule_time", ""), data.get("schedule_username", ""))
            changed.append("scheduling defaults (not enabled - the password is never read from this file)")
        if any(k in data for k in _RETRY_FIELDS):
            cs.save_download_settings(conn, str(data.get("download_retry_count", "")),
                                      str(data.get("download_retry_delay", "")))
            changed.append("download retry")
        if "log_retention_days" in data:
            cs.save_log_retention(conn, str(data.get("log_retention_days", "")))
            changed.append("log retention")
        if any(k in data for k in _FOLDER_FIELDS):
            cs.save_folders(conn, data.get("rpi_folder", ""), data.get("asset_folder", ""), data_dir)
            changed.append("local folders")
    except cs.SettingsError as exc:
        raise InstallConfigError(f"The install configuration file was rejected: {exc}") from None
    return changed
