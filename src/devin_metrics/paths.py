"""Locations of Devin's local stores.

Session data lives in ``$XDG_DATA_HOME/devin`` on Linux (default
``~/.local/share/devin``); GUI ACP logs live in ``$XDG_CONFIG_HOME/Devin/User``
(default ``~/.config/Devin/User``). Windows shares an APPDATA root; explicit
store paths remain available for non-standard installs.
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
        (root for root in candidates if sessions_db_path(root).is_file()),
        candidates[0],
    )


def default_config_dir(
    environ: dict[str, str] | None = None, platform: str | None = None
) -> Path:
    env = os.environ if environ is None else environ
    plat = sys.platform if platform is None else platform
    override = env.get("DEVIN_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if plat.startswith("win"):
        appdata = env.get("APPDATA")
        return Path(appdata) / "Devin" if appdata else Path.home() / "AppData" / "Roaming" / "Devin"
    if plat == "darwin":
        return Path.home() / "Library" / "Application Support" / "Devin"
    config_home = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    candidates = [config_home / "Devin", config_home / "devin"]
    return next(
        (root for root in candidates if (root / "User").exists()),
        candidates[0],
    )


def sessions_db_path(root: Path) -> Path:
    return root / "cli" / "sessions.db"


def acp_messages_dir(root: Path) -> Path:
    return root / "User" / "acp-messages"


def default_acp_messages_dir(
    data_dir: str | Path | None = None,
    environ: dict[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    if data_dir is not None:
        return acp_messages_dir(Path(data_dir).expanduser())
    config_dir = default_config_dir(environ=environ, platform=platform)
    candidate = acp_messages_dir(config_dir)
    if candidate.is_dir():
        return candidate
    legacy = acp_messages_dir(default_data_dir(environ=environ, platform=platform))
    return legacy if legacy.is_dir() else candidate
