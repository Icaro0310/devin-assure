"""Render aggregation dicts — GitHub-flavored markdown tables, or raw JSON.

All functions are pure: they take the dicts produced by ``aggregate.py`` and
return strings. ``None`` renders as ``-`` (unknown is never shown as zero).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any


def dumps_json(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def fmt_usd(v: float | None) -> str:
    return "-" if v is None else f"${v:.4f}"


def fmt_int(v: int | None) -> str:
    return "-" if v is None else f"{v:,}"


def fmt_duration_ms(ms: int | None) -> str:
    if ms is None:
        return "-"
    s = ms // 1000
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def _line(cells: Sequence[str]) -> str:
        return "| " + " | ".join(
            str(c).ljust(widths[i]) for i, c in enumerate(cells)
        ) + " |"

    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    return "\n".join([_line(headers), sep, *[_line(r) for r in rows]])


def _session_rows(sessions: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [
            s["title"] or s["id"][:8],
            s["project"],
            fmt_duration_ms(s["duration_ms"]),
            fmt_usd(s["cost_usd"]),
            fmt_int(s["messages"]),
            fmt_int(s["tool_calls"]),
        ]
        for s in sessions
    ]


_SESSION_HEADERS = ["SESSION", "PROJECT", "DURATION", "COST", "MSGS", "TOOLS"]


def render_summary_md(s: dict[str, Any]) -> str:
    stats = [
        ["Sessions", fmt_int(s["sessions"])],
        ["Sessions with cost data", fmt_int(s["sessions_with_cost"])],
        ["Messages", fmt_int(s["messages"])],
        ["Tool calls", fmt_int(s["tool_calls"])],
        ["Total duration", fmt_duration_ms(s["duration_ms_total"])],
        ["Cost (all acp usage)", fmt_usd(s["cost_usd_total"])],
        ["Cost (attributed to sessions)", fmt_usd(s["cost_usd_by_sessions"])],
        ["Input tokens", fmt_int(s["input_tokens_total"])],
        ["Output tokens", fmt_int(s["output_tokens_total"])],
        ["Avg duration / session", fmt_duration_ms(
            int(s["avg_duration_ms"]) if s["avg_duration_ms"] is not None else None
        )],
        ["Avg messages / session", (
            f"{s['avg_messages']:.1f}" if s["avg_messages"] is not None else "-"
        )],
        ["Avg cost / session", fmt_usd(s["avg_cost_usd"])],
        ["First activity", s["first_seen"] or "-"],
        ["Last activity", s["last_seen"] or "-"],
        ["acp-messages store", (
            f"{s['acp_db_files']} db(s), {s['matched_sessions']} matched, "
            f"{s['orphan_dbs']} orphan, {s['acp_db_errors']} unreadable"
            if s["acp_available"] else "not found"
        )],
    ]
    parts = ["## devin-metrics summary", "", md_table(["METRIC", "VALUE"], stats)]

    models = s.get("models") or []
    if models:
        parts += [
            "",
            "### By model",
            "",
            md_table(
                ["MODEL", "SESSIONS", "USAGE ROWS", "COST", "INPUT TOK", "OUTPUT TOK"],
                [
                    [
                        m["model"],
                        fmt_int(m["sessions"]),
                        fmt_int(m["usage_records"]),
                        fmt_usd(m["cost_usd"]),
                        fmt_int(m["input_tokens"]),
                        fmt_int(m["output_tokens"]),
                    ]
                    for m in models
                ],
            ),
        ]
    if s["top_longest"]:
        parts += [
            "",
            "### Longest sessions",
            "",
            md_table(_SESSION_HEADERS, _session_rows(s["top_longest"])),
        ]
    if s["top_costliest"]:
        parts += [
            "",
            "### Most expensive sessions",
            "",
            md_table(_SESSION_HEADERS, _session_rows(s["top_costliest"])),
        ]
    return "\n".join(parts)


def render_projects_md(rows: list[dict[str, Any]]) -> str:
    return md_table(
        ["PROJECT", "SESSIONS", "COST", "INPUT TOK", "OUTPUT TOK", "MSGS",
         "TOOLS", "DURATION"],
        [
            [
                r["project"],
                fmt_int(r["sessions"]),
                fmt_usd(r["cost_usd"]),
                fmt_int(r["input_tokens"]),
                fmt_int(r["output_tokens"]),
                fmt_int(r["messages"]),
                fmt_int(r["tool_calls"]),
                fmt_duration_ms(r["duration_ms"]),
            ]
            for r in rows
        ],
    )


def render_daily_md(rows: list[dict[str, Any]]) -> str:
    return md_table(
        ["DATE", "SESSIONS", "MSGS", "TOOLS", "DURATION", "COST"],
        [
            [
                r["date"],
                fmt_int(r["sessions"]),
                fmt_int(r["messages"]),
                fmt_int(r["tool_calls"]),
                fmt_duration_ms(r["duration_ms"]),
                fmt_usd(r["cost_usd"]),
            ]
            for r in rows
        ],
    )
