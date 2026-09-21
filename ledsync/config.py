"""Application paths and runtime configuration.

Every filesystem location the app uses is derived here, so relocating the data
directory (BRD Section 14: "Database location") is a one-place change.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

DB_FILENAME = "ledsync.db"


def default_data_dir() -> Path:
    """%LOCALAPPDATA%\\LEDAssetSync on Windows (per-user).

    If scheduled runs must work with no user logged in (BRD Section 36, still
    open), a machine-wide location such as %PROGRAMDATA% will be needed instead.
    Decide before Phase 12.
    """
    override = os.environ.get("LEDSYNC_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "LEDAssetSync"
    return Path.home() / ".ledassetsync"


@dataclass(frozen=True)
class Config:
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / DB_FILENAME


def load() -> Config:
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Config(data_dir=data_dir)


# --- development-only settings from a git-ignored .env -------------------------------------
# The Storage Account key is entered in Settings on a real venue machine. While developing
# from source, STORAGE_ACCOUNT_NAME / STORAGE_ACCOUNT_KEY / STORAGE_CONTAINER may instead be
# supplied by a local .env in the project root (never committed - see .gitignore) or by the
# process environment. An installed (frozen) build never reads a .env.

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOTENV_PATH = PROJECT_ROOT / ".env"


def read_dotenv(path: Path | None = None) -> dict[str, str]:
    """Parse simple KEY=VALUE lines (blank lines and # comments ignored, optional quotes)."""
    if getattr(sys, "frozen", False):
        return {}
    target = DOTENV_PATH if path is None else path
    values: dict[str, str] = {}
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name.replace("_", "").isalnum():
            values[name] = value
    return values


def dev_setting_with_source(name: str, dotenv: dict[str, str] | None = None) -> tuple[str, str]:
    """(value, where it came from): the process environment first, then the .env file.
    DEVELOPMENT ONLY - an installed (frozen) build ignores both, so a stray variable on a venue
    machine can never silently configure it."""
    if getattr(sys, "frozen", False):
        return "", ""
    if os.environ.get(name):
        return os.environ[name], "the process environment"
    value = (dotenv if dotenv is not None else read_dotenv()).get(name, "")
    return (value, "the development .env file") if value else ("", "")


def dev_setting(name: str, dotenv: dict[str, str] | None = None) -> str:
    return dev_setting_with_source(name, dotenv)[0]
