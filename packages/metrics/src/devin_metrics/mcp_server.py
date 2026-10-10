"""devin-metrics as an MCP server: the ``--json`` payloads as one tool.

One tool, ``metrics_query`` — reads the local session stores and returns
the same JSON payloads as ``devin-metrics summary|projects|daily --json``.
Read-only: the stores are only ever opened for reading; nothing is
written, no network, no sessions are spawned.

The logic lives in :func:`do_query`, unit-testable without a running
server or the ``mcp`` package. ``build_server()`` wraps it — needs the
``mcp`` extra: ``pip install 'devin-metrics[mcp]'``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from devin_internals.schema import SchemaError

from devin_metrics.aggregate import by_day, by_project, summarize
from devin_metrics.collect import collect
from devin_metrics.paths import (
    default_acp_messages_dir,
    default_data_dir,
    sessions_db_path,
)

_KINDS = ("summary", "projects", "daily")


def _resolve_stores(sessions_db: str, acp_dir: str) -> tuple[Path, Path]:
    """Same resolution as the CLI: explicit paths or platform defaults."""
    db = (
        Path(sessions_db).expanduser()
        if sessions_db
        else sessions_db_path(default_data_dir())
    )
    acp = Path(acp_dir).expanduser() if acp_dir else default_acp_messages_dir()
    return db, acp


def do_query(
    kind: str = "summary",
    days: int = 30,
    sessions_db: str = "",
    acp_dir: str = "",
) -> dict | list:
    """Query the session stores and return the ``--json`` payload.

    ``kind`` selects the rollup: ``summary`` (headline counts, per-model
    table, top-5 lists), ``projects`` (one row per working directory) or
    ``daily`` (one row per UTC activity day — ``days`` keeps the N most
    recent activity days, 0 or negative means all). Failures the CLI
    reports as exit 1 (missing or unreadable sessions.db) surface here
    as ``{"error": ...}``; a missing acp dir degrades gracefully, same as
    the CLI.
    """
    if kind not in _KINDS:
        return {
            "error": "usage",
            "detail": f"unknown kind {kind!r} — expected one of {_KINDS}",
        }
    db, acp = _resolve_stores(sessions_db, acp_dir)
    try:
        snap = collect(db, acp)
    except SchemaError as exc:
        return {"error": "bad_store", "detail": str(exc)[:500]}
    except (sqlite3.Error, OSError) as exc:
        return {"error": "io", "detail": str(exc)[:500]}
    if kind == "summary":
        return summarize(snap)
    if kind == "projects":
        return by_project(snap)
    return by_day(snap, days=days if days > 0 else None)


def _err(error: Exception) -> dict:
    return {"error": type(error).__name__, "detail": str(error)[:500]}


def _make_app(name: str):
    """Return an MCP server app across SDK versions.

    mcp 2.x renamed FastMCP -> MCPServer; both expose the same .tool()
    decorator and .run(transport='stdio'). Support whichever is installed.
    """
    try:  # mcp 2.x
        from mcp.server.mcpserver import MCPServer
        return MCPServer(name)
    except ImportError:
        pass
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP
        return FastMCP(name)
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "The MCP server needs the 'mcp' extra: "
            "pip install 'devin-metrics[mcp]'"
        ) from e


def build_server():
    """Build the MCP server app with every devin-metrics tool registered."""
    server = _make_app("devin-metrics")

    @server.tool()
    def metrics_query(
        kind: str = "summary",
        days: int = 30,
        sessions_db: str = "",
        acp_dir: str = "",
    ) -> dict | list[Any]:
        """Read-only usage metrics from Devin's local session stores —
        same JSON as ``devin-metrics <kind> --json``. ``kind``:
        ``summary`` (headline counts, per-model table, top-5 longest and
        costliest sessions), ``projects`` (per-project session/activity
        rows) or ``daily`` (per-day activity; ``days`` keeps the N most
        recent activity days, 0 = all). ``sessions_db``/``acp_dir``
        default to the platform Devin data dirs. Cost/token fields are
        ``null`` when the local ACP logs carry none — never a bill.
        """
        try:
            return do_query(
                kind=kind,
                days=days,
                sessions_db=sessions_db,
                acp_dir=acp_dir,
            )
        except Exception as error:  # noqa: BLE001 — tool boundary must not raise
            return _err(error)

    return server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
