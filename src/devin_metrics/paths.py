"""Locations of Devin's local stores.

Windows: ``%APPDATA%/devin`` · macOS: ``~/Library/Application Support/devin`` ·
Linux/other: ``~/.config/devin``. Inside the data dir the relevant layout is::

    cli/sessions.db                 CLI session store (schema-ledgered)
    User/acp-messages/<uuid>.db     per-session GUI message logs

Same convention as ``devin-doctor`` — the ecosystem agrees on one data-dir map.
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


def sessions_db_path(root: Path) -> Path:
    return root / "cli" / "sessions.db"


def acp_messages_dir(root: Path) -> Path:
    return root / "User" / "acp-messages"
