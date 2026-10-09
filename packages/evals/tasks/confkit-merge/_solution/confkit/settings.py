"""Settings load/dump helpers."""

from __future__ import annotations

import json
from pathlib import Path


def load_settings(path: str) -> dict:
    """Read a JSON settings file into a dict."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump_settings(settings: dict, path: str) -> None:
    """Write a settings dict as indented JSON."""
    Path(path).write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def merge_settings(base: dict, override: dict) -> dict:
    """Deep-merge ``override`` onto ``base``; returns a new dict."""
    merged = dict(base)
    for key, value in override.items():
        if (key in merged
                and isinstance(merged[key], dict)
                and isinstance(value, dict)):
            merged[key] = merge_settings(merged[key], value)
        else:
            merged[key] = value
    return merged
