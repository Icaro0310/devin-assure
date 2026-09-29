"""Default locations of Devin's local session store.

Windows: ``%APPDATA%/devin`` · macOS: ``~/Library/Application Support/devin`` ·
Linux/other: ``~/.config/devin``. The sessions DB lives at
``<data dir>/cli/sessions.db`` (same layout as devin-internals-spec).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_data_dir() -> Path:
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "devin"
        return Path.home() / "AppData" / "Roaming" / "devin"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "devin"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "devin"


def default_sessions_db() -> Path:
    return default_data_dir() / "cli" / "sessions.db"
