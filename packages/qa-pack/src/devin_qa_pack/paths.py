"""Default locations of Devin's local session store.

Windows: ``%APPDATA%/devin`` · macOS: ``~/Library/Application Support/devin`` ·
Linux: ``$XDG_DATA_HOME/devin`` (default ``~/.local/share/devin``), with the
older config-dir location as a fallback.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_data_dir(
    environ: dict[str, str] | None = None, platform: str | None = None
) -> Path:
    env = os.environ if environ is None else environ
    plat = sys.platform if platform is None else platform
    override = env.get("DEVIN_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if plat.startswith("win"):
        appdata = env.get("APPDATA")
        if appdata:
            return Path(appdata) / "devin"
        return Path.home() / "AppData" / "Roaming" / "devin"
    if plat == "darwin":
        return Path.home() / "Library" / "Application Support" / "devin"
    data_home = Path(env.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    config_home = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    candidates = [data_home / "devin", config_home / "devin", Path.home() / "devin"]
    return next(
        (root for root in candidates if (root / "cli" / "sessions.db").is_file()),
        candidates[0],
    )


def default_sessions_db(
    data_dir: str | Path | None = None,
    environ: dict[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    return (Path(data_dir).expanduser() if data_dir else default_data_dir(
        environ=environ, platform=platform
    )) / "cli" / "sessions.db"
