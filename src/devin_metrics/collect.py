"""Collect raw metric rows from Devin's local stores — read-only, no network.

Two sources:

- ``cli/sessions.db`` — session rows (id, ``working_directory`` = project,
  declared model, timestamps) plus ``message_nodes``/``tool_call_state``
  counts, via :class:`devin_internals.parsers.SessionsStore`.
- ``User/acp-messages/<session-uuid>.db`` — per-session ACP message log whose
  ``messages.payload`` JSON is *assumed* to carry model/cost/token fields
  (see ``docs/SCHEMA.md``). The whole assumption lives in exactly one
  function: :func:`extract_usage`.

acp DBs are linked to sessions by **filename stem** = session id.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devin_internals.parsers import AcpMessagesStore, SessionsStore
from devin_internals.schema import SchemaError


@dataclass(frozen=True)
class UsageRecord:
    """Cost/model/token data extracted from one acp ``messages`` row.

    Any field may be ``None`` — payloads are only partially trusted.
    """

    session_id: str
    position: int
    kind: str
    model: str | None
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class SessionMetrics:
    """One session row enriched with message/tool counts and acp usage."""

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


@dataclass(frozen=True)
class MetricsSnapshot:
    """Everything the aggregation layer needs."""

    sessions_db: str
    acp_dir: str | None
    acp_available: bool
    acp_db_files: int
    acp_db_errors: int
    matched_sessions: int
    orphan_dbs: int
    sessions: tuple[SessionMetrics, ...]
    usage: tuple[UsageRecord, ...]


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def extract_usage(
    session_id: str, position: int, kind: str, payload: str
) -> UsageRecord | None:
    """THE adapter: assumed acp ``messages.payload`` shape → UsageRecord.

    Assumed shape (documented in ``docs/SCHEMA.md``, *unverified* against the
    real store — see "Known gaps")::

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


def _scan_acp_dir(acp_dir: Path | None) -> tuple[list[UsageRecord], int, int, int]:
    """Return (usage records, db file count, parse errors, orphan dbs)."""
    if acp_dir is None or not acp_dir.is_dir():
        return [], 0, 0, 0
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
    return records, files, errors, 0  # orphan count computed by caller


def _sum_or_none(values: list[float | int | None]) -> float | int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def collect(sessions_db: str | Path, acp_dir: str | Path | None) -> MetricsSnapshot:
    """Read both stores and return a flat, JSON-ready snapshot.

    ``acp_dir`` may be missing or ``None`` — cost/token fields then stay
    ``None`` and the snapshot reports ``acp_available=False``. A missing or
    unreadable ``sessions_db`` raises :class:`SchemaError` (it is the core
    store; there is nothing to degrade to).
    """
    sessions_db = Path(sessions_db).expanduser()
    acp_path = Path(acp_dir).expanduser() if acp_dir is not None else None

    with SessionsStore(sessions_db) as store:
        sessions = store.sessions()
        msg_counts: dict[str, int] = {}
        for node in store.message_nodes():
            msg_counts[node.session_id] = msg_counts.get(node.session_id, 0) + 1
        tool_counts: dict[str, int] = {}
        for tc in store.tool_call_state():
            tool_counts[tc.session_id] = tool_counts.get(tc.session_id, 0) + 1

    usage, files, errors, _ = _scan_acp_dir(acp_path)
    acp_available = acp_path is not None and acp_path.is_dir()

    by_session: dict[str, list[UsageRecord]] = {}
    for rec in usage:
        by_session.setdefault(rec.session_id, []).append(rec)

    known_ids = {s.id for s in sessions}
    orphan_dbs = len(
        {rec.session_id for rec in usage} - known_ids
    )
    metrics = tuple(
        SessionMetrics(
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
        for s in sessions
    )
    return MetricsSnapshot(
        sessions_db=str(sessions_db),
        acp_dir=str(acp_path) if acp_path is not None else None,
        acp_available=acp_available,
        acp_db_files=files,
        acp_db_errors=errors,
        matched_sessions=sum(1 for s in metrics if s.id in by_session),
        orphan_dbs=orphan_dbs,
        sessions=metrics,
        usage=tuple(usage),
    )
