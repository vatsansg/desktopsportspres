"""Application paths and runtime configuration.

Every filesystem location the app uses is derived here, so relocating the data
directory (BRD Section 14: "Database location") is a one-place change.
"""

import os
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
