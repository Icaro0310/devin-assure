"""Rollups over a :class:`MetricsSnapshot` — pure functions, JSON-ready dicts.

Attribution rules:

- Sessions group by ``working_directory`` (the project) — free with Devin's
  session store.
- Day buckets use the session's ``created_at`` in UTC.
- ``cost_usd_total`` counts **all** acp usage rows (including orphan dbs with
  no session row); ``cost_usd_by_sessions`` counts only matched sessions.
- ``cost_usd is None`` means *unknown* (no acp data), never zero.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from devin_metrics.collect import MetricsSnapshot, SessionMetrics


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def session_to_dict(s: SessionMetrics) -> dict[str, Any]:
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
        "context_tokens": s.context_tokens,
    }


def _sum_or_none(values: Iterable[float | int | None]) -> float | int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _sum(values: Iterable[float | int]) -> float | int:
    return sum(values)


# -- summaries ---------------------------------------------------------------


def summarize(snap: MetricsSnapshot) -> dict[str, Any]:
    sessions = list(snap.sessions)
    n = len(sessions)
    with_cost = [s for s in sessions if s.cost_usd is not None]
    return {
        "sessions": n,
        "sessions_with_cost": len(with_cost),
        "hidden_sessions": sum(1 for s in sessions if s.hidden),
        "first_seen": _day(min(s.created_at for s in sessions)) if n else None,
        "last_seen": _day(max(s.last_activity_at for s in sessions)) if n else None,
        "messages": _sum(s.n_messages for s in sessions),
        "tool_calls": _sum(s.n_tool_calls for s in sessions),
        "duration_ms_total": _sum(s.duration_ms for s in sessions),
        "cost_usd_total": _sum_or_none(u.cost_usd for u in snap.usage),
        "cost_usd_by_sessions": _sum_or_none(s.cost_usd for s in sessions),
        "input_tokens_total": _sum_or_none(u.input_tokens for u in snap.usage),
        "output_tokens_total": _sum_or_none(u.output_tokens for u in snap.usage),
        "context_tokens_peak": max(
            (s.context_tokens for s in sessions
             if s.context_tokens is not None),
            default=None,
        ),
        "sessions_with_context": sum(
            1 for s in sessions if s.context_tokens is not None
        ),
        "sessions_with_commits": len({r.session_id for r in snap.commits}),
        "unique_commit_shas": len({r.sha for r in snap.commits}),
        "avg_duration_ms": (_sum(s.duration_ms for s in sessions) / n) if n else None,
        "avg_messages": (_sum(s.n_messages for s in sessions) / n) if n else None,
        "avg_cost_usd": (
            _sum(s.cost_usd for s in with_cost) / len(with_cost)  # type: ignore[arg-type]
            if with_cost
            else None
        ),
        "acp_available": snap.acp_available,
        "acp_db_files": snap.acp_db_files,
        "acp_db_errors": snap.acp_db_errors,
        "matched_sessions": snap.matched_sessions,
        "orphan_dbs": snap.orphan_dbs,
        "top_longest": top_sessions(snap, n=5, key="duration_ms"),
        "top_costliest": top_sessions(snap, n=5, key="cost_usd"),
        "models": by_model(snap),
    }


def by_project(snap: MetricsSnapshot) -> list[dict[str, Any]]:
    groups: dict[str, list[SessionMetrics]] = {}
    for s in snap.sessions:
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


def by_model(snap: MetricsSnapshot) -> list[dict[str, Any]]:
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

    for s in snap.sessions:
        _row(s.model)["sessions"] += 1
    for u in snap.usage:
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


def by_day(snap: MetricsSnapshot, days: int | None = None) -> list[dict[str, Any]]:
    """Sessions bucketed by UTC creation date.

    ``days=N`` keeps the N most recent *activity* days (anchored on the latest
    day in the data, not on wall-clock now — deterministic for fixtures and
    for inspecting old installs alike).
    """
    groups: dict[str, list[SessionMetrics]] = {}
    for s in snap.sessions:
        groups.setdefault(_day(s.created_at), []).append(s)
    dates = sorted(groups)
    if days is not None:
        dates = dates[-days:] if days > 0 else []
    return [
        {
            "date": d,
            "sessions": len(groups[d]),
            "messages": _sum(s.n_messages for s in groups[d]),
            "tool_calls": _sum(s.n_tool_calls for s in groups[d]),
            "duration_ms": _sum(s.duration_ms for s in groups[d]),
            "cost_usd": _sum_or_none(s.cost_usd for s in groups[d]),
        }
        for d in dates
    ]


def commits_report(snap: MetricsSnapshot) -> dict[str, Any]:
    """Session→commit correlation over ``snap.commits`` (tool_call_state refs).

    - ``sessions`` lists every session id with ≥1 ref, most commits first;
      ``top_sessions`` is the first 5 of that same ordering. ``title`` and
      ``project`` join back to session rows and stay ``None`` for refs whose
      session is no longer in ``sessions`` (same orphan policy as acp usage).
      Each session row keeps the sha→repo linkage as
      ``commits: [{"sha": ..., "repos": [...]}]``.
    - ``repos`` counts *unique SHAs* per repo URL. A SHA published under
      several repo URLs credits **every** repo (mirrors, forks); it still
      counts once in the global ``unique_shas``. A bare SHA never seen
      inside a ``/commit/<sha>`` URL appears under no repo.
    """
    titles = {s.id: s.title for s in snap.sessions}
    projects = {s.id: s.working_directory for s in snap.sessions}
    by_session: dict[str, dict[str, set[str]]] = {}
    sha_repos: dict[str, set[str]] = {}
    for ref in snap.commits:
        entry = by_session.setdefault(
            ref.session_id, {"shas": set(), "repos": set()}
        )
        entry["shas"].add(ref.sha)
        if ref.repo_url:
            entry["repos"].add(ref.repo_url)
            sha_repos.setdefault(ref.sha, set()).add(ref.repo_url)
    sessions = [
        {
            "id": sid,
            "title": titles.get(sid),
            "project": projects.get(sid),
            "n_commits": len(entry["shas"]),
            "commits": [
                {"sha": sha, "repos": sorted(sha_repos.get(sha, ()))}
                for sha in sorted(entry["shas"])
            ],
            "repos": sorted(entry["repos"]),
        }
        for sid, entry in by_session.items()
    ]
    sessions.sort(key=lambda r: (-r["n_commits"], r["id"]))
    repo_shas: dict[str, set[str]] = {}
    for sha, repos_for_sha in sha_repos.items():
        for repo in repos_for_sha:
            repo_shas.setdefault(repo, set()).add(sha)
    repos = [
        {
            "repo": repo,
            "unique_shas": len(shas),
            "sessions": sum(
                1 for e in by_session.values() if repo in e["repos"]
            ),
        }
        for repo, shas in repo_shas.items()
    ]
    repos.sort(key=lambda r: (-r["unique_shas"], r["repo"]))
    return {
        "sessions_with_commits": len(by_session),
        "unique_shas": len({r.sha for r in snap.commits}),
        "commit_refs": len(snap.commits),
        "repos": repos,
        "sessions": sessions,
        "top_sessions": sessions[:5],
    }


def watch_report(
    snap: MetricsSnapshot,
    session_warn: int = 400_000,
    daily_warn: int = 2_000_000,
) -> dict[str, Any]:
    """Advisory context-guard findings (ME-2) — warns, never blocks.

    Cost is unverifiable in local stores (ME-3), so the guard watches
    ``context_tokens`` (peak prompt size per session) — the only real
    resource signal that persists. Thresholds are advisory; callers decide
    the exit code. Shared by the ``watch`` command and ``digest --weekly``.
    """
    findings: list[dict[str, Any]] = []
    by_ctx = sorted(
        (s for s in snap.sessions if s.context_tokens is not None),
        key=lambda s: s.context_tokens or 0, reverse=True)
    over_session = [s for s in by_ctx if s.context_tokens > session_warn]
    days: dict[str, int] = {}
    for s in snap.sessions:
        if s.context_tokens:
            d = _day(s.created_at)
            days[d] = days.get(d, 0) + s.context_tokens
    over_days = {d: v for d, v in days.items() if v > daily_warn}
    for s in over_session:
        findings.append({"kind": "session", "session_id": s.id,
                         "context_tokens": s.context_tokens,
                         "threshold": session_warn})
    for d, v in sorted(over_days.items()):
        findings.append({"kind": "day", "date": d, "context_tokens": v,
                         "threshold": daily_warn})
    return {
        "advisory": True,
        "thresholds": {"session_warn": session_warn,
                       "daily_warn": daily_warn},
        "sessions_observed": len(by_ctx),
        "findings": findings,
        "note": "advisory only — context_tokens is peak prompt size, "
                "not cost (local stores have no cost data, verified ME-3)",
    }


def top_sessions(
    snap: MetricsSnapshot, n: int = 5, key: str = "duration_ms"
) -> list[dict[str, Any]]:
    """Top-N sessions. ``key``: ``duration_ms`` or ``cost_usd``.

    For ``cost_usd`` sessions with unknown cost are excluded entirely.
    """
    if key == "cost_usd":
        pool = [s for s in snap.sessions if s.cost_usd is not None]
        ordered = sorted(pool, key=lambda s: s.cost_usd or 0, reverse=True)
    elif key == "duration_ms":
        ordered = sorted(snap.sessions, key=lambda s: s.duration_ms, reverse=True)
    else:
        raise ValueError(f"top_sessions: unknown key {key!r}")
    return [session_to_dict(s) for s in ordered[:n]]
