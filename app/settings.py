"""Persisted application settings (local folder, Drive folder, options)."""

import json
import os
from dataclasses import asdict, dataclass
from typing import Optional

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "googlesinc")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")

DEFAULT_LOCAL_ROOT = os.path.join(os.path.expanduser("~"), "GoogleSinc")
DEFAULT_DRIVE_FOLDER = "GoogleSinc"


@dataclass
class AppSettings:
    local_root: str = DEFAULT_LOCAL_ROOT
    drive_folder: str = DEFAULT_DRIVE_FOLDER
    execute_deletes: bool = True
    poll_interval_seconds: int = 60


def load_settings() -> AppSettings:
    if not os.path.exists(SETTINGS_PATH):
        return AppSettings()
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    defaults = asdict(AppSettings())
    merged = {key: data.get(key, value) for key, value in defaults.items()}
    return AppSettings(**merged)


def save_settings(settings: AppSettings) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as handle:
        json.dump(asdict(settings), handle, indent=2)
