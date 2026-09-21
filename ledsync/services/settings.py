"""Cloud Storage settings (BRD Section 14) - the ONLY module that reads or writes them.

Stored in `application_settings`:
    cloud_storage_account   e.g. sasportspresentation
    cloud_container         default YEAR container used to find a new event, e.g. 2026
    cloud_access_key        the Storage Account access key
    rpi_folder              where files from the event's `RPI` folder are saved on this computer, in a
                            sub-folder per event (empty = the default `RPI` folder in the application data folder)
    asset_folder            where the Table / LED files are saved: <folder>\\<Event ID>\\Table N\\<LED type>
                            (empty = the default `Events` folder in the application data folder)

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
