"""Rework (churn) metrics over a built ``graph.db`` (ME-4).

Churn = rework: distinct tool calls in the same session that touched the
same file again. Read straight from the knowledge graph's
``file_touched`` edges (each edge is one tool_call → file, owned by a
session), so this needs a ``devin-graph build`` first — the graph is the
source of truth, this module never touches sessions.db.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _connect_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    return con


def churn_report(graph_db: str | Path) -> dict[str, Any]:
    """Per-session and per-model rework stats from a graph.db.

    Returns dict with ``sessions`` (one row per session that reworked at
    least one file), ``models`` (aggregated by the session node's model
    attr) and ``top_files`` (most-reworked paths overall).
    """
    path = Path(graph_db).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"{path}: no graph — run 'devin-graph build --graph {path}'")
    con = _connect_ro(path)
    try:
        _require_schema(con)
        reworked = con.execute(
            "SELECT e.owner, e.dst, COUNT(*) AS calls"
            " FROM edges e WHERE e.kind = 'file_touched'"
            " GROUP BY e.owner, e.dst HAVING calls > 1"
        ).fetchall()
        touched = con.execute(
            "SELECT owner, COUNT(DISTINCT dst) FROM edges"
            " WHERE kind = 'file_touched' GROUP BY owner"
        ).fetchall()
        models = {
            r[0]: _model_of(r[1])
            for r in con.execute(
                "SELECT key, attrs FROM nodes WHERE kind = 'session'"
            ).fetchall()
        }
        paths = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT id, key FROM nodes WHERE kind = 'file'"
            ).fetchall()
        }
    finally:
        con.close()

    distinct_files = {owner: n for owner, n in touched}
    per_session: dict[str, dict[str, Any]] = {}
    file_calls: dict[str, int] = {}
    for session_id, file_id, calls in reworked:
        row = per_session.setdefault(session_id, {
            "session_id": session_id,
            "model": models.get(session_id),
            "files_touched": distinct_files.get(session_id, 0),
            "reworked_files": 0,
            "repeat_calls": 0,
        })
        row["reworked_files"] += 1
        row["repeat_calls"] += calls - 1
        file_calls[file_id] = file_calls.get(file_id, 0) + calls - 1

    sessions = sorted(
        per_session.values(), key=lambda r: -r["repeat_calls"])

    per_model: dict[str, dict[str, Any]] = {}
    for row in sessions:
        m = row["model"] or "(unknown)"
        agg = per_model.setdefault(m, {
            "model": m, "sessions_with_churn": 0,
            "reworked_files": 0, "repeat_calls": 0})
        agg["sessions_with_churn"] += 1
        agg["reworked_files"] += row["reworked_files"]
        agg["repeat_calls"] += row["repeat_calls"]

    return {
        "graph": str(path),
        "sessions_with_churn": len(sessions),
        "sessions": sessions,
        "models": sorted(
            per_model.values(), key=lambda r: -r["repeat_calls"]),
        "top_files": [
            {"file": paths.get(fid, fid), "repeat_calls": n}
            for fid, n in sorted(
                file_calls.items(), key=lambda kv: -kv[1])[:20]
        ],
    }


def _require_schema(con: sqlite3.Connection) -> None:
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    cols = {r[1] for r in con.execute("PRAGMA table_info(edges)")}
    if not ({"nodes", "edges"} <= tables and {"kind", "src", "dst", "owner"}
            <= cols):
        raise ValueError(
            f"{con}: not a devin-graph database — "
            "expected nodes/edges(kind, src, dst, owner) from "
            "'devin-graph build'")


def _model_of(attrs_json: str) -> str | None:
    try:
        attrs = json.loads(attrs_json)
    except (TypeError, ValueError):
        return None
    m = attrs.get("model")
    return m if isinstance(m, str) and m else None


def render_churn(report: dict[str, Any]) -> str:
    """Human-readable churn table."""
    lines = [
        f"churn report — {report['graph']}",
        f"sessions with rework: {report['sessions_with_churn']}",
        "",
        f"{'MODEL':<24} {'SESS':>5} {'REWORKED FILES':>15} {'REPEAT CALLS':>13}",
    ]
    for m in report["models"]:
        lines.append(
            f"{m['model']:<24} {m['sessions_with_churn']:>5} "
            f"{m['reworked_files']:>15} {m['repeat_calls']:>13}")
    if report["top_files"]:
        lines += ["", "most reworked files:"]
        for f in report["top_files"][:10]:
            lines.append(f"  {f['repeat_calls']:>4}×  {f['file']}")
    return "\n".join(lines)
