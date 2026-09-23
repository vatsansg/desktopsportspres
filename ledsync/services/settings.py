"""Application Settings (BRD Section 14) - the ONLY module that reads or writes them (all of `application_settings`
except the admin credential, which stays in `auth.py`).

Stored in `application_settings`:
    cloud_storage_account   e.g. sasportspresentation
    cloud_container         default YEAR container used to find a new event, e.g. 2026
    cloud_access_key        the Storage Account access key
    rpi_folder              where files from the event's `RPI` folder are saved on this computer, in a
                            sub-folder per event (empty = the default `RPI` folder in the application data folder)
    asset_folder            where the Table / LED files are saved: <folder>\\<Event ID>\\Table N\\<LED type>
                            (empty = the default `Events` folder in the application data folder)
    download_retry_count    extra tries after a transient download/push failure (Phase 10; blank = built-in default)
    download_retry_delay    seconds paused before each retry (Phase 10; blank = built-in default)
    log_retention_days      keep operational/exception log rows for this many days (Phase 10; blank = forever)
    schedule_*              day(s)/time/enabled for a scheduled run (Phase 10 stores it; Phase 12 runs it)
    email_*                 recipient/enabled/connection string for the completion notification (Phase 10 stores
                            it; Phase 11 sends it, via Azure Communication Services - see the note further down)

OWNER DECISION (21/09/26): the access key is stored as PLAIN TEXT in the database. This
departs from BRD 33.3 ("must not be stored as plain text where avoidable") and is recorded
as an owner-approved deviation. Mitigations that remain: the key is never sent back to the
browser, never logged, never placed in an error message, is reachable only through this
module, and the database lives in the current user's profile folder. All reads and writes
of the key go through `_read_secret` / `_write_secret` below, so moving to Windows DPAPI
later is a change in exactly one place.

SCOPE GUARD: the administrator credential (`admin_*` keys, BRD 6.1) is NOT handled here and
this module refuses to touch any key it does not own; only `services/auth.py` may.
"""

import base64
import binascii
import re
import sqlite3
from dataclasses import dataclass, field

from pathlib import Path

from .. import config as app_config
from . import mappings, oplog

KEY_ACCOUNT = "cloud_storage_account"
KEY_CONTAINER = "cloud_container"
KEY_ACCESS = "cloud_access_key"
KEY_RPI_FOLDER = "rpi_folder"
KEY_ASSET_FOLDER = "asset_folder"
OWNED_KEYS = frozenset({KEY_ACCOUNT, KEY_CONTAINER, KEY_ACCESS, KEY_RPI_FOLDER, KEY_ASSET_FOLDER})

DEV_ACCOUNT = "STORAGE_ACCOUNT_NAME"
DEV_CONTAINER = "STORAGE_CONTAINER"
DEV_KEY = "STORAGE_ACCOUNT_KEY"

_ACCOUNT_RE = re.compile(r"^[a-z0-9]{3,24}$")                       # Azure storage account naming rule
_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){1,61}[a-z0-9]$")   # 3-63, no double hyphen
_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{40,120}={0,2}$")              # base64; Azure keys are 88 characters


class SettingsError(ValueError):
    """A rejected settings value; the message is safe to show and never contains the key."""


@dataclass(frozen=True)
class CloudView:
    """What a PAGE may know about the settings: everything except the key itself, so a future
    template edit cannot print it."""
    account: str
    container: str
    has_key: bool
    configured: bool
    account_source: str     # "" = saved in Settings, otherwise where the development value came from
    key_source: str

    @property
    def account_from_dev_env(self) -> bool:
        return bool(self.account_source)

    @property
    def key_from_dev_env(self) -> bool:
        return bool(self.key_source)


@dataclass(frozen=True)
class CloudSettings:
    account: str = ""
    container: str = ""
    access_key: str = field(default="", repr=False)   # never appears in a repr, log or page
    account_source: str = ""
    key_source: str = ""

    @property
    def account_from_dev_env(self) -> bool:
        return bool(self.account_source)

    @property
    def key_from_dev_env(self) -> bool:
        return bool(self.key_source)

    @property
    def has_key(self) -> bool:
        return bool(self.access_key)

    def public(self) -> CloudView:
        return CloudView(self.account, self.container, self.has_key, self.configured,
                         self.account_source, self.key_source)

    @property
    def configured(self) -> bool:
        return bool(self.account and self.access_key)

    @property
    def blob_endpoint(self) -> str:
        return f"https://{self.account}.blob.core.windows.net"


# --- the single place the key is read/written ---------------------------------------------

def _get(conn: sqlite3.Connection, key: str) -> str:
    if key not in OWNED_KEYS:
        raise PermissionError("settings.py may only touch its own keys")
    row = conn.execute("SELECT setting_value FROM application_settings WHERE setting_name = ?", (key,)).fetchone()
    return (row["setting_value"] or "") if row else ""


def _put(conn: sqlite3.Connection, key: str, value: str) -> None:
    if key not in OWNED_KEYS:
        raise PermissionError("settings.py may only touch its own keys")
    conn.execute(
        "INSERT INTO application_settings (setting_name, setting_value) VALUES (?, ?) "
        "ON CONFLICT(setting_name) DO UPDATE SET setting_value = excluded.setting_value",
        (key, value),
    )


def _read_secret(conn: sqlite3.Connection) -> str:
    return _get(conn, KEY_ACCESS)


def _write_secret(conn: sqlite3.Connection, secret: str) -> None:
    _put(conn, KEY_ACCESS, secret)


# --- validation ---------------------------------------------------------------------------------

def validate_account(value: str) -> str:
    if looks_like_secret(value):                       # refuse (never store or show) a key pasted in the wrong box
        raise SettingsError("The storage account name must be 3 to 24 lower-case letters and numbers.")
    value = (value or "").strip().lower()
    if not _ACCOUNT_RE.match(value):
        raise SettingsError("The storage account name must be 3 to 24 lower-case letters and numbers.")
    return value


def validate_container(value: str) -> str:
    if looks_like_secret(value):
        raise SettingsError("The container name must be 3 to 63 lower-case letters, numbers and single hyphens "
                            "(for example 2026).")
    value = (value or "").strip().lower()
    if not value:
        return ""
    if not _CONTAINER_RE.match(value):
        raise SettingsError("The container name must be 3 to 63 lower-case letters, numbers and single hyphens "
                            "(for example 2026).")
    return value


def validate_key(value: str) -> str:
    value = (value or "").strip()
    try:
        ok = bool(_KEY_RE.match(value)) and bool(base64.b64decode(value, validate=True))
    except (binascii.Error, ValueError):
        ok = False
    if not ok:
        raise SettingsError("That does not look like a storage account access key "
                            "(it should be about 88 letters, numbers, + / and =).")
    return value


def looks_like_secret(value: str) -> bool:
    """Long, key-shaped text: 40+ base64/URL-safe characters that include an upper-case letter or
    + / =. (Real names of accounts, containers and events are short and lower-case or numeric.)"""
    text = (value or "").strip()
    return len(text) >= 40 and bool(re.fullmatch(r"[A-Za-z0-9+/=_-]+", text)) and bool(re.search(r"[A-Z+/=]", text))


def redact_if_secret_like(value: str) -> str:
    """Text about to be shown back in a form field. A key pasted into the wrong field (Event ID,
    account, container) must never be echoed into the page, so anything key-shaped is blanked."""
    return "" if looks_like_secret(value) else (value or "")


# --- public API ------------------------------------------------------------------------------------

def load_cloud(conn: sqlite3.Connection, dotenv: dict[str, str] | None = None) -> CloudSettings:
    """Saved settings, falling back per field to the development .env / environment when a
    field has not been saved (so a developer need not retype the key in Settings)."""
    account, container, key = _get(conn, KEY_ACCOUNT), _get(conn, KEY_CONTAINER), _read_secret(conn)
    account_source = key_source = ""
    if not account:
        account, account_source = _dev_value(DEV_ACCOUNT, dotenv, validate_account)
    if not container:
        container, _ = _dev_value(DEV_CONTAINER, dotenv, validate_container)
    if not key:
        key, key_source = _dev_value(DEV_KEY, dotenv, validate_key)
    return CloudSettings(account, container, key, account_source, key_source)


def _dev_value(name: str, dotenv, validator) -> tuple[str, str]:
    """A development fallback value, held to the SAME validation as anything typed into Settings
    (a hostile or mistyped variable must never redirect signed requests to another host)."""
    value, source = app_config.dev_setting_with_source(name, dotenv)
    if not value.strip():
        return "", ""
    try:
        return validator(value), source
    except SettingsError:
        return "", ""


def save_cloud(conn: sqlite3.Connection, account: str, container: str, new_key: str | None) -> list[str]:
    """Validate and save. `new_key` empty/None keeps the existing key. Returns the NAMES of the
    fields that changed (never values) and writes the audit row in the same transaction."""
    account = validate_account(account)
    container = validate_container(container)
    key = validate_key(new_key) if (new_key or "").strip() else None

    changed = []
    try:
        if _get(conn, KEY_ACCOUNT) != account:
            _put(conn, KEY_ACCOUNT, account)
            changed.append("storage account")
        if _get(conn, KEY_CONTAINER) != container:
            _put(conn, KEY_CONTAINER, container)
            changed.append("container")
        if key is not None and key != _read_secret(conn):
            _write_secret(conn, key)
            changed.append("access key")
        oplog.add(conn, "Settings Changed", "Success",
                  "Cloud storage settings saved" + (f" ({', '.join(changed)} changed)." if changed else " (no change)."))
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise SettingsError("The settings could not be saved. Try again.") from None
    return changed


# --- local folders (RPI) -------------------------------------------------------------------------------

DEFAULT_RPI_NAME = "RPI"


def default_rpi_folder(data_dir) -> Path:
    return Path(data_dir) / DEFAULT_RPI_NAME


@dataclass(frozen=True)
class RpiFolder:
    saved: str               # what the operator entered ("" = use the default)
    effective: Path          # where files really go
    is_default: bool


def load_rpi_folder(conn: sqlite3.Connection, data_dir) -> RpiFolder:
    saved = _get(conn, KEY_RPI_FOLDER).strip()
    return RpiFolder(saved, Path(saved) if saved else default_rpi_folder(data_dir), not saved)


def validate_rpi_folder(text: str, data_dir) -> str:
    """"" (use the default) or a validated absolute folder. A folder inside the application data
    folder is refused - the default already lives there - so a downloaded file can never sit
    beside the database."""
    value = (text or "").strip()
    if not value:
        return ""
    try:
        return mappings.validate_shared_folder(value, "RPI folder", forbidden_roots=(data_dir,))
    except mappings.MappingError as err:
        raise SettingsError(f"{err} Leave the box empty to use the default RPI folder.") from None


def save_rpi_folder(conn: sqlite3.Connection, text: str, data_dir) -> bool:
    """Validate and save; returns whether anything changed. The audit row is in the same transaction."""
    value = validate_rpi_folder(text, data_dir)
    try:
        changed = _get(conn, KEY_RPI_FOLDER) != value
        if changed:
            _put(conn, KEY_RPI_FOLDER, value)
        oplog.add(conn, "Settings Changed", "Success",
                  "Local folders saved (RPI folder changed)." if changed else "Local folders saved (no change).")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise SettingsError("The settings could not be saved. Try again.") from None
    return changed


DEFAULT_ASSET_NAME = "Events"


def default_asset_folder(data_dir) -> Path:
    return Path(data_dir) / DEFAULT_ASSET_NAME


def load_asset_folder(conn: sqlite3.Connection, data_dir) -> RpiFolder:
    saved = _get(conn, KEY_ASSET_FOLDER).strip()
    return RpiFolder(saved, Path(saved) if saved else default_asset_folder(data_dir), not saved)


def _norm(path) -> str:
    """A comparable spelling of a folder: a local path's real location (8.3 names and junctions resolved) in one case."""
    import os
    return os.path.normcase(os.path.normpath(mappings.resolve_local(str(path)))).rstrip("\\") + "\\"


def _check_apart(rpi: Path, assets: Path, data_dir) -> None:
    a, b, data = _norm(rpi), _norm(assets), _norm(data_dir)
    if data.startswith(a) or data.startswith(b):
        raise SettingsError("A download folder cannot contain the application's own data folder.")
    if a.startswith(b) or b.startswith(a):
        raise SettingsError("The RPI folder and the asset folder must be two different folders, and neither can be inside "
                            "the other.")


def save_folders(conn: sqlite3.Connection, rpi_text: str, asset_text: str, data_dir) -> list[str]:
    """Validate and save both local folders in one transaction. Returns the NAMES of what changed."""
    rpi_value = validate_rpi_folder(rpi_text, data_dir)
    asset_value = _validate_asset_folder(asset_text, data_dir)
    _check_apart(Path(rpi_value) if rpi_value else default_rpi_folder(data_dir),
                 Path(asset_value) if asset_value else default_asset_folder(data_dir), data_dir)
    changed = []
    try:
        if _get(conn, KEY_RPI_FOLDER) != rpi_value:
            _put(conn, KEY_RPI_FOLDER, rpi_value)
            changed.append("RPI folder")
        if _get(conn, KEY_ASSET_FOLDER) != asset_value:
            _put(conn, KEY_ASSET_FOLDER, asset_value)
            changed.append("asset folder")
        oplog.add(conn, "Settings Changed", "Success",
                  f"Local folders saved ({', '.join(changed)} changed)." if changed else "Local folders saved (no change).")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise SettingsError("The settings could not be saved. Try again.") from None
    return changed


def _validate_asset_folder(text: str, data_dir) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    try:
        return mappings.validate_shared_folder(value, "Asset folder", forbidden_roots=(data_dir,))
    except mappings.MappingError as err:
        raise SettingsError(f"{err} Leave the box empty to use the default asset folder.") from None


# --- download settings: retry count / delay (BRD 14, Phase 10) ---------------------------------------------------
#
# Blank (the default, like the two folders above) means "use the application's own default", which is read live
# from `transfer.RETRY_COUNT` / `transfer.RETRY_DELAY` rather than a value frozen here - so a test that monkeypatches
# those constants (or a future change to the built-in default) is reflected without also updating this module.

KEY_RETRY_COUNT = "download_retry_count"
KEY_RETRY_DELAY = "download_retry_delay"
RETRY_COUNT_RANGE = (0, 5)
RETRY_DELAY_RANGE = (0.0, 60.0)


@dataclass(frozen=True)
class DownloadSettings:
    retry_count: int                   # effective value (the saved one, or the built-in default)
    retry_delay: float
    saved_retry_count: str             # "" = using the default; otherwise what was saved, as typed
    saved_retry_delay: str

    @property
    def retry_count_is_default(self) -> bool:
        return not self.saved_retry_count

    @property
    def retry_delay_is_default(self) -> bool:
        return not self.saved_retry_delay


def load_download_settings(conn: sqlite3.Connection) -> DownloadSettings:
    from . import transfer                                     # local import: transfer does not import settings
    raw_count, raw_delay = _get(conn, KEY_RETRY_COUNT).strip(), _get(conn, KEY_RETRY_DELAY).strip()
    return DownloadSettings(
        int(raw_count) if raw_count else transfer.RETRY_COUNT, float(raw_delay) if raw_delay else transfer.RETRY_DELAY,
        raw_count, raw_delay)


def validate_retry_count(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    try:
        n = int(value)
    except ValueError:
        raise SettingsError(f"Enter a whole number of retries, {RETRY_COUNT_RANGE[0]} to {RETRY_COUNT_RANGE[1]}, "
                            "or leave it blank for the default.") from None
    if not (RETRY_COUNT_RANGE[0] <= n <= RETRY_COUNT_RANGE[1]):
        raise SettingsError(f"The number of retries must be {RETRY_COUNT_RANGE[0]} to {RETRY_COUNT_RANGE[1]}.")
    return str(n)


def validate_retry_delay(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    try:
        n = float(value)
    except ValueError:
        raise SettingsError(f"Enter the delay in seconds, {RETRY_DELAY_RANGE[0]:.0f} to {RETRY_DELAY_RANGE[1]:.0f}, "
                            "or leave it blank for the default.") from None
    if not (RETRY_DELAY_RANGE[0] <= n <= RETRY_DELAY_RANGE[1]):
        raise SettingsError(f"The delay between retries must be {RETRY_DELAY_RANGE[0]:.0f} to "
                            f"{RETRY_DELAY_RANGE[1]:.0f} seconds.")
    return str(n)


def save_download_settings(conn: sqlite3.Connection, retry_count_text: str, retry_delay_text: str) -> list[str]:
    count, delay = validate_retry_count(retry_count_text), validate_retry_delay(retry_delay_text)
    changed = []
    try:
        if _get(conn, KEY_RETRY_COUNT) != count:
            _put(conn, KEY_RETRY_COUNT, count)
            changed.append("retry count")
        if _get(conn, KEY_RETRY_DELAY) != delay:
            _put(conn, KEY_RETRY_DELAY, delay)
            changed.append("retry delay")
        oplog.add(conn, "Settings Changed", "Success",
                  f"Download settings saved ({', '.join(changed)} changed)." if changed else "Download settings saved (no change).")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise SettingsError("The settings could not be saved. Try again.") from None
    return changed


# --- log retention (carried from Phase 9, F-65) ------------------------------------------------------------------

KEY_LOG_RETENTION = "log_retention_days"
LOG_RETENTION_RANGE = (1, 3650)


@dataclass(frozen=True)
class RetentionSettings:
    saved: str                 # "" = forever
    days: int | None           # None = forever


def load_log_retention(conn: sqlite3.Connection) -> RetentionSettings:
    saved = _get(conn, KEY_LOG_RETENTION).strip()
    return RetentionSettings(saved, int(saved) if saved else None)


def validate_log_retention(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    try:
        n = int(value)
    except ValueError:
        raise SettingsError("Enter a whole number of days, or leave it blank to keep logs forever.") from None
    if not (LOG_RETENTION_RANGE[0] <= n <= LOG_RETENTION_RANGE[1]):
        raise SettingsError(f"Keep logs for {LOG_RETENTION_RANGE[0]} to {LOG_RETENTION_RANGE[1]} days, "
                            "or leave it blank to keep them forever.")
    return str(n)


def save_log_retention(conn: sqlite3.Connection, text: str) -> bool:
    value = validate_log_retention(text)
    try:
        changed = _get(conn, KEY_LOG_RETENTION) != value
        if changed:
            _put(conn, KEY_LOG_RETENTION, value)
        oplog.add(conn, "Settings Changed", "Success",
                  (f"Log retention saved (keep for {value} day(s))." if value else "Log retention saved (keep forever).")
                  if changed else "Log retention saved (no change).")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise SettingsError("The settings could not be saved. Try again.") from None
    return changed


# --- scheduling settings (BRD 14/22; storage only - Phase 12 runs the schedule) ------------------------------------

KEY_SCHEDULE_ENABLED = "schedule_enabled"
KEY_SCHEDULE_DAYS = "schedule_days"
KEY_SCHEDULE_TIME = "schedule_time"
SCHEDULE_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class ScheduleSettings:
    enabled: bool
    days: tuple                # a subset of SCHEDULE_DAYS, in week order
    time: str                  # "" or "HH:MM" (24-hour)


def load_schedule(conn: sqlite3.Connection) -> ScheduleSettings:
    raw_days = _get(conn, KEY_SCHEDULE_DAYS)
    days = tuple(d for d in SCHEDULE_DAYS if d in raw_days.split(",")) if raw_days else ()
    return ScheduleSettings(_get(conn, KEY_SCHEDULE_ENABLED) == "1", days, _get(conn, KEY_SCHEDULE_TIME))


def validate_schedule_time(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    if not _TIME_RE.match(value):
        raise SettingsError("Enter the time as HH:MM in 24-hour format (for example 02:30), or leave it blank.")
    return value


def _validate_schedule_days(values) -> str:
    return ",".join(d for d in SCHEDULE_DAYS if d in (values or ()))    # fixed order; anything else forged is dropped


def save_schedule(conn: sqlite3.Connection, enabled: bool, days, time_text: str) -> list[str]:
    days_value, time_value = _validate_schedule_days(days), validate_schedule_time(time_text)
    if enabled and (not days_value or not time_value):
        raise SettingsError("Choose at least one day and a time before enabling the schedule.")
    enabled_value = "1" if enabled else "0"
    changed = []
    try:
        if _get(conn, KEY_SCHEDULE_ENABLED) != enabled_value:
            _put(conn, KEY_SCHEDULE_ENABLED, enabled_value)
            changed.append("enabled")
        if _get(conn, KEY_SCHEDULE_DAYS) != days_value:
            _put(conn, KEY_SCHEDULE_DAYS, days_value)
            changed.append("days")
        if _get(conn, KEY_SCHEDULE_TIME) != time_value:
            _put(conn, KEY_SCHEDULE_TIME, time_value)
            changed.append("time")
        oplog.add(conn, "Settings Changed", "Success",
                  f"Scheduling settings saved ({', '.join(changed)} changed)." if changed else "Scheduling settings saved (no change).")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise SettingsError("The settings could not be saved. Try again.") from None
    return changed


# --- email settings (BRD 14/23; Phase 11 sends the notification) -----------------------------------------------------
#
# DEVIATION (22 Sep 2026): BRD Section 14 lists SMTP server/port/username/authentication fields, but the Desktop BRD
# Addendum A Section 39.3 (confirmed 20 Sep 2026, before Phase 10 started) supersedes that: the email mechanism is
# Azure Communication Services, the same service the web application uses. SMTP fields would be built only to be
# thrown away once Phase 11 arrives, so this page stores what Phase 11 actually needs instead: the recipient, a
# sender address, whether notifications are on, and a connection string (handled exactly like the storage access
# key - never echoed back, never logged).
#
# DEVIATION (23 Sep 2026, Phase 11): ACS requires a verified "from" address on every send - the BRD's superseded
# SMTP-field list implied one too (a server/port/username triple has an implicit envelope sender) but Section 14's
# post-39.3 field list did not carry one forward explicitly, and Phase 10 did not add one. Added here as "Sender
# address" - my default; tell me if wrong. Required together with the recipient when notifications are enabled.

KEY_EMAIL_ENABLED = "email_enabled"
KEY_EMAIL_RECIPIENT = "email_recipient"
KEY_EMAIL_SENDER = "email_sender"
KEY_EMAIL_CONNECTION = "email_connection_string"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")     # a plain shape check, not full RFC 5322
_CONNECTION_LENGTH = (20, 2000)


@dataclass(frozen=True)
class EmailSettings:
    enabled: bool
    recipient: str
    sender: str
    has_connection: bool


def load_email(conn: sqlite3.Connection) -> EmailSettings:
    return EmailSettings(_get(conn, KEY_EMAIL_ENABLED) == "1", _get(conn, KEY_EMAIL_RECIPIENT),
                         _get(conn, KEY_EMAIL_SENDER), bool(_get(conn, KEY_EMAIL_CONNECTION)))


@dataclass(frozen=True)
class EmailCredentials:
    """Internal use only (the notifier, email_notify.py) - carries the real connection string.
    Never pass this to a template; `load_email`/`EmailSettings` is the page-safe view."""
    enabled: bool
    recipient: str
    sender: str
    connection_string: str = field(default="", repr=False)   # never appears in a repr, log or page


def load_email_secret(conn: sqlite3.Connection) -> EmailCredentials:
    return EmailCredentials(_get(conn, KEY_EMAIL_ENABLED) == "1", _get(conn, KEY_EMAIL_RECIPIENT),
                            _get(conn, KEY_EMAIL_SENDER), _get(conn, KEY_EMAIL_CONNECTION))


_MAX_RECIPIENTS = 20


def split_email_recipients(text: str) -> list[str]:
    """The stored/typed recipient value -> its individual addresses (comma-separated; blank entries
    from stray commas or spacing are dropped)."""
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def validate_email_recipient(text: str) -> str:
    """One or more addresses, comma-separated (owner request, 23/09/26 - a run can notify more than
    one IT recipient). Stored/returned normalised as 'a@x.com, b@y.com'; every address is validated
    individually, so one bad address in the list refuses the whole save."""
    addresses = split_email_recipients(text)
    if not addresses:
        return ""
    if len(addresses) > _MAX_RECIPIENTS:
        raise SettingsError(f"Enter no more than {_MAX_RECIPIENTS} recipient addresses.")
    for address in addresses:
        if looks_like_secret(address) or len(address) > 200 or not _EMAIL_RE.match(address):
            # Never echo `address` back here: a secret-shaped paste is exactly the case this guards
            # against, and a message repeating it would leak it right back onto the page/exception log.
            raise SettingsError("Enter one or more valid email addresses, separated by commas.")
    return ", ".join(addresses)


def validate_email_sender(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    if looks_like_secret(value) or len(value) > 200 or not _EMAIL_RE.match(value):
        raise SettingsError("Enter a valid sender address, or leave it blank.")
    return value


def validate_email_connection(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    if not (_CONNECTION_LENGTH[0] <= len(value) <= _CONNECTION_LENGTH[1]):
        raise SettingsError("That does not look like an Azure Communication Services connection string.")
    return value


def save_email(conn: sqlite3.Connection, enabled: bool, recipient_text: str, sender_text: str,
              connection_text: str) -> list[str]:
    """`connection_text` empty keeps the existing connection string (like the storage access key)."""
    recipient = validate_email_recipient(recipient_text)
    sender = validate_email_sender(sender_text)
    connection = validate_email_connection(connection_text) if (connection_text or "").strip() else None
    if enabled and not recipient:
        raise SettingsError("Enter the notification email address before turning notifications on.")
    if enabled and not sender:
        raise SettingsError("Enter the sender address before turning notifications on.")
    if enabled and not (connection or _get(conn, KEY_EMAIL_CONNECTION)):
        raise SettingsError("Enter the Azure Communication Services connection string before turning notifications on.")
    enabled_value = "1" if enabled else "0"
    changed = []
    try:
        if _get(conn, KEY_EMAIL_ENABLED) != enabled_value:
            _put(conn, KEY_EMAIL_ENABLED, enabled_value)
            changed.append("enabled")
        if _get(conn, KEY_EMAIL_RECIPIENT) != recipient:
            _put(conn, KEY_EMAIL_RECIPIENT, recipient)
            changed.append("recipient")
        if _get(conn, KEY_EMAIL_SENDER) != sender:
            _put(conn, KEY_EMAIL_SENDER, sender)
            changed.append("sender")
        if connection is not None and connection != _get(conn, KEY_EMAIL_CONNECTION):
            _put(conn, KEY_EMAIL_CONNECTION, connection)
            changed.append("connection string")
        oplog.add(conn, "Settings Changed", "Success",
                  f"Email settings saved ({', '.join(changed)} changed)." if changed else "Email settings saved (no change).")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise SettingsError("The settings could not be saved. Try again.") from None
    return changed


# All the keys this module is allowed to touch (see the SCOPE GUARD note at the top of the file).
# Appended here, after every KEY_* constant above is defined.
OWNED_KEYS = OWNED_KEYS | {
    KEY_RETRY_COUNT, KEY_RETRY_DELAY, KEY_LOG_RETENTION,
    KEY_SCHEDULE_ENABLED, KEY_SCHEDULE_DAYS, KEY_SCHEDULE_TIME,
    KEY_EMAIL_ENABLED, KEY_EMAIL_RECIPIENT, KEY_EMAIL_SENDER, KEY_EMAIL_CONNECTION,
}
