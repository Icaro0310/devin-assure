"""Collect Devin usage into a normalized stats dict — read-only, no network.

Produces the same aggregates as ``devin-metrics`` (per-day sessions/cost,
per-project, per-model, top sessions) but returns a single JSON-ready dict
shaped for embedding into the dashboard HTML. Aggregation logic is duplicated
locally on purpose: cross-repo dependencies are allowed only on
``devin-internals-spec``.

Attribution rules (identical to devin-metrics):

- Sessions group by ``working_directory`` (the project).
- Day buckets use the session's ``created_at`` in UTC.
- ``cost_usd_total`` counts **all** acp usage rows (including orphan dbs with
  no session row); ``cost_usd_by_sessions`` counts only matched sessions.
- ``cost_usd is None`` means *unknown* (no acp data), never zero.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from datetime import datetime, timezone

from devin_internals.parsers import AcpMessagesStore, SessionsStore
from devin_internals.schema import SchemaError

from devin_metrics.dashboard import __version__
from devin_metrics.paths import (
    acp_messages_dir,
    default_data_dir as _default_data_dir,
    sessions_db_path,
)


# -- store locations ---------------------------------------------------------

def default_data_dir() -> Path:
    return _default_data_dir()


def default_sessions_db(root: Path) -> Path:
    return sessions_db_path(root)


def default_acp_dir(root: Path) -> Path:
    return acp_messages_dir(root)


# -- acp usage extraction ----------------------------------------------------
# THE adapter: assumed acp ``messages.payload`` shape → usage row. The whole
# assumption lives in exactly this one function (same contract as
# devin-metrics.collect.extract_usage).

@dataclass(frozen=True)
class UsageRecord:
    session_id: str
    position: int
    kind: str
    model: str | None
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class SessionRow:
    id: str
    working_directory: str
    model: str | None
    title: str | None
    created_at: int
    last_activity_at: int
    duration_ms: int
    hidden: bool
    n_messages: int
    n_tool_calls: int
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def extract_usage(
    session_id: str, position: int, kind: str, payload: str
) -> UsageRecord | None:
    """Assumed acp ``messages.payload`` shape::

        {"model": "<model-id>", "cost_usd": <number>,
         "usage": {"input_tokens": <int>, "output_tokens": <int>}}

    Returns ``None`` for non-JSON payloads or rows carrying none of those
    fields, so plain user/tool chatter never leaks into usage stats. If the
    real payload shape turns out to differ, this function is the *only* place
    that changes.
    """
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    model = data.get("model")
    record = UsageRecord(
        session_id=session_id,
        position=position,
        kind=kind,
        model=model if isinstance(model, str) else None,
        cost_usd=_num(data.get("cost_usd")),
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
    )
    if (
        record.model is None
        and record.cost_usd is None
        and record.input_tokens is None
        and record.output_tokens is None
    ):
        return None
    return record


def _scan_acp_dir(acp_dir: Path | None) -> tuple[list[UsageRecord], int, int]:
    """Return (usage records, db file count, parse errors)."""
    if acp_dir is None or not acp_dir.is_dir():
        return [], 0, 0
    records: list[UsageRecord] = []
    files = errors = 0
    for db in sorted(acp_dir.glob("*.db")):
        files += 1
        try:
            with AcpMessagesStore(db) as store:
                for msg in store.messages():
                    rec = extract_usage(db.stem, msg.position, msg.kind, msg.payload)
                    if rec is not None:
                        records.append(rec)
        except (SchemaError, sqlite3.Error, OSError):
            errors += 1
    return records, files, errors


# -- rollups -----------------------------------------------------------------

def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def _sum(values: Iterable[float | int]) -> float | int:
    return sum(values)


def _sum_or_none(values: Iterable[float | int | None]) -> float | int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _session_dict(s: SessionRow) -> dict[str, Any]:
    return {
        "id": s.id,
        "title": s.title,
        "project": s.working_directory,
        "model": s.model,
        "created": _day(s.created_at),
        "created_at": s.created_at,
        "duration_ms": s.duration_ms,
        "messages": s.n_messages,
        "tool_calls": s.n_tool_calls,
        "cost_usd": s.cost_usd,
        "input_tokens": s.input_tokens,
        "output_tokens": s.output_tokens,
    }


def _by_day(sessions: list[SessionRow]) -> list[dict[str, Any]]:
    groups: dict[str, list[SessionRow]] = {}
    for s in sessions:
        groups.setdefault(_day(s.created_at), []).append(s)
    return [
        {
            "date": d,
            "sessions": len(groups[d]),
            "messages": _sum(s.n_messages for s in groups[d]),
            "tool_calls": _sum(s.n_tool_calls for s in groups[d]),
            "duration_ms": _sum(s.duration_ms for s in groups[d]),
            "cost_usd": _sum_or_none(s.cost_usd for s in groups[d]),
        }
        for d in sorted(groups)
    ]


def _by_project(sessions: list[SessionRow]) -> list[dict[str, Any]]:
    groups: dict[str, list[SessionRow]] = {}
    for s in sessions:
        groups.setdefault(s.working_directory, []).append(s)
    rows = [
        {
            "project": wd,
            "sessions": len(members),
            "messages": _sum(s.n_messages for s in members),
            "tool_calls": _sum(s.n_tool_calls for s in members),
            "duration_ms": _sum(s.duration_ms for s in members),
            "cost_usd": _sum_or_none(s.cost_usd for s in members),
            "input_tokens": _sum_or_none(s.input_tokens for s in members),
            "output_tokens": _sum_or_none(s.output_tokens for s in members),
        }
        for wd, members in groups.items()
    ]
    rows.sort(
        key=lambda r: (r["cost_usd"] is not None, r["cost_usd"] or 0, r["sessions"]),
        reverse=True,
    )
    return rows


def _by_model(
    sessions: list[SessionRow], usage: list[UsageRecord]
) -> list[dict[str, Any]]:
    """Per-model stats merging session-declared models and acp usage rows."""
    rows: dict[str, dict[str, Any]] = {}

    def _row(model: str | None) -> dict[str, Any]:
        name = model or "(unknown)"
        return rows.setdefault(
            name,
            {
                "model": name,
                "sessions": 0,
                "usage_records": 0,
                "cost_usd": None,
                "input_tokens": None,
                "output_tokens": None,
            },
        )

    for s in sessions:
        _row(s.model)["sessions"] += 1
    for u in usage:
        r = _row(u.model)
        r["usage_records"] += 1
        if u.cost_usd is not None:
            r["cost_usd"] = (r["cost_usd"] or 0.0) + u.cost_usd
        if u.input_tokens is not None:
            r["input_tokens"] = (r["input_tokens"] or 0) + u.input_tokens
        if u.output_tokens is not None:
            r["output_tokens"] = (r["output_tokens"] or 0) + u.output_tokens
    out = list(rows.values())
    out.sort(
        key=lambda r: (r["cost_usd"] is not None, r["cost_usd"] or 0, r["sessions"]),
        reverse=True,
    )
    return out


def _top(
    sessions: list[SessionRow], n: int = 5, key: str = "duration_ms"
) -> list[dict[str, Any]]:
    if key == "cost_usd":
        pool = [s for s in sessions if s.cost_usd is not None]
        ordered = sorted(pool, key=lambda s: s.cost_usd or 0, reverse=True)
    else:
        ordered = sorted(sessions, key=lambda s: s.duration_ms, reverse=True)
    return [_session_dict(s) for s in ordered[:n]]


def _summary(
    sessions: list[SessionRow], usage: list[UsageRecord]
) -> dict[str, Any]:
    with_cost = [s for s in sessions if s.cost_usd is not None]
    return {
        "sessions": len(sessions),
        "sessions_with_cost": len(with_cost),
        "hidden_sessions": sum(1 for s in sessions if s.hidden),
        "first_seen": _day(min(s.created_at for s in sessions))
        if sessions
        else None,
        "last_seen": _day(max(s.last_activity_at for s in sessions))
        if sessions
        else None,
        "messages": _sum(s.n_messages for s in sessions),
        "tool_calls": _sum(s.n_tool_calls for s in sessions),
        "duration_ms_total": _sum(s.duration_ms for s in sessions),
        "cost_usd_total": _sum_or_none(u.cost_usd for u in usage),
        "cost_usd_by_sessions": _sum_or_none(s.cost_usd for s in sessions),
        "input_tokens_total": _sum_or_none(u.input_tokens for u in usage),
        "output_tokens_total": _sum_or_none(u.output_tokens for u in usage),
        "avg_cost_usd": (
            _sum(s.cost_usd for s in with_cost) / len(with_cost)  # type: ignore[arg-type]
            if with_cost
            else None
        ),
    }


# -- entry point -------------------------------------------------------------

def collect_stats(
    sessions_db: str | Path, acp_dir: str | Path | None = None
) -> dict[str, Any]:
    """Read both stores and return the normalized stats dict.

    ``acp_dir`` may be missing or ``None`` — cost/token fields then stay
    ``None`` and ``source.acp_available`` is ``False``. A missing or
    unreadable ``sessions_db`` raises :class:`SchemaError` (it is the core
    store; there is nothing to degrade to).
    """
    sessions_db = Path(sessions_db).expanduser()
    acp_path = Path(acp_dir).expanduser() if acp_dir is not None else None

    with SessionsStore(sessions_db) as store:
        schema_version = store.schema_info["schema_version"]
        raw_sessions = store.sessions()
        msg_counts: dict[str, int] = {}
        for node in store.message_nodes():
            msg_counts[node.session_id] = msg_counts.get(node.session_id, 0) + 1
        tool_counts: dict[str, int] = {}
        for tc in store.tool_call_state():
            tool_counts[tc.session_id] = tool_counts.get(tc.session_id, 0) + 1

    usage, files, errors = _scan_acp_dir(acp_path)
    acp_available = acp_path is not None and acp_path.is_dir()

    by_session: dict[str, list[UsageRecord]] = {}
    for rec in usage:
        by_session.setdefault(rec.session_id, []).append(rec)

    known_ids = {s.id for s in raw_sessions}
    orphan_dbs = len({rec.session_id for rec in usage} - known_ids)
    sessions = [
        SessionRow(
            id=s.id,
            working_directory=s.working_directory,
            model=s.model,
            title=s.title,
            created_at=s.created_at,
            last_activity_at=s.last_activity_at,
            duration_ms=max(0, s.last_activity_at - s.created_at),
            hidden=s.hidden,
            n_messages=msg_counts.get(s.id, 0),
            n_tool_calls=tool_counts.get(s.id, 0),
            cost_usd=_sum_or_none([u.cost_usd for u in by_session.get(s.id, [])]),
            input_tokens=_sum_or_none(
                [u.input_tokens for u in by_session.get(s.id, [])]
            ),
            output_tokens=_sum_or_none(
                [u.output_tokens for u in by_session.get(s.id, [])]
            ),
        )
        for s in raw_sessions
    ]

    return {
        "tool": "devin-dashboard",
        "version": __version__,
        "source": {
            "sessions_db": str(sessions_db),
            "acp_dir": str(acp_path) if acp_path is not None else None,
            "acp_available": acp_available,
            "acp_db_files": files,
            "acp_db_errors": errors,
            "matched_sessions": sum(1 for s in sessions if s.id in by_session),
            "orphan_dbs": orphan_dbs,
            "schema_version": schema_version,
        },
        "summary": _summary(sessions, usage),
        "daily": _by_day(sessions),
        "projects": _by_project(sessions),
        "models": _by_model(sessions, usage),
        "top_sessions": {
            "longest": _top(sessions, n=5, key="duration_ms"),
            "costliest": _top(sessions, n=5, key="cost_usd"),
        },
    }
