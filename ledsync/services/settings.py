"""Cloud Storage settings (BRD Section 14) - the ONLY module that reads or writes them.

Stored in `application_settings`:
    cloud_storage_account   e.g. sasportspresentation
    cloud_container         default YEAR container used to find a new event, e.g. 2026
    cloud_access_key        the Storage Account access key

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

import re
import sqlite3
from dataclasses import dataclass, field

from .. import config as app_config
from . import oplog

KEY_ACCOUNT = "cloud_storage_account"
KEY_CONTAINER = "cloud_container"
KEY_ACCESS = "cloud_access_key"
OWNED_KEYS = frozenset({KEY_ACCOUNT, KEY_CONTAINER, KEY_ACCESS})

DEV_ACCOUNT = "STORAGE_ACCOUNT_NAME"
DEV_CONTAINER = "STORAGE_CONTAINER"
DEV_KEY = "STORAGE_ACCOUNT_KEY"

_ACCOUNT_RE = re.compile(r"^[a-z0-9]{3,24}$")                       # Azure storage account naming rule
_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){1,61}[a-z0-9]$")   # 3-63, no double hyphen
_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{40,120}={0,2}$")              # base64; Azure keys are 88 characters


class SettingsError(ValueError):
    """A rejected settings value; the message is safe to show and never contains the key."""


@dataclass(frozen=True)
class CloudSettings:
    account: str = ""
    container: str = ""
    access_key: str = field(default="", repr=False)   # never appears in a repr, log or page
    account_from_dev_env: bool = False
    key_from_dev_env: bool = False

    @property
    def has_key(self) -> bool:
        return bool(self.access_key)

    @property
    def configured(self) -> bool:
        return bool(self.account and self.access_key)

    @property
    def blob_endpoint(self) -> str:
        return f"https://{self.account}.blob.core.windows.net"


# --- the single place the key is read/written ---------------------------------------------

def _get(conn: sqlite3.Connection, key: str) -> str:
    assert key in OWNED_KEYS, "settings.py may only touch its own keys"
    row = conn.execute("SELECT setting_value FROM application_settings WHERE setting_name = ?", (key,)).fetchone()
    return (row["setting_value"] or "") if row else ""


def _put(conn: sqlite3.Connection, key: str, value: str) -> None:
    assert key in OWNED_KEYS, "settings.py may only touch its own keys"
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
    value = (value or "").strip().lower()
    if not _ACCOUNT_RE.match(value):
        raise SettingsError("The storage account name must be 3 to 24 lower-case letters and numbers.")
    return value


def validate_container(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    if not _CONTAINER_RE.match(value):
        raise SettingsError("The container name must be 3 to 63 lower-case letters, numbers and single hyphens "
                            "(for example 2026).")
    return value


def validate_key(value: str) -> str:
    value = (value or "").strip()
    if not _KEY_RE.match(value):
        raise SettingsError("That does not look like a storage account access key "
                            "(it should be about 88 letters, numbers, + / and =).")
    return value


# --- public API ------------------------------------------------------------------------------------

def load_cloud(conn: sqlite3.Connection, dotenv: dict[str, str] | None = None) -> CloudSettings:
    """Saved settings, falling back per field to the development .env / environment when a
    field has not been saved (so a developer need not retype the key in Settings)."""
    account, container, key = _get(conn, KEY_ACCOUNT), _get(conn, KEY_CONTAINER), _read_secret(conn)
    dev_account = dev_key = False
    if not account:
        account = app_config.dev_setting(DEV_ACCOUNT, dotenv).strip().lower()
        dev_account = bool(account)
    if not container:
        container = app_config.dev_setting(DEV_CONTAINER, dotenv).strip().lower()
    if not key:
        key = app_config.dev_setting(DEV_KEY, dotenv).strip()
        dev_key = bool(key)
    return CloudSettings(account, container, key, dev_account, dev_key)


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
