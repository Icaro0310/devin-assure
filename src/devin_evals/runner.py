"""Offline replay: score eval cases against a recorded ``sessions.db``.

The runner is read-only and deterministic — same db + same evals directory
always produces byte-identical ``report.json`` / ``report.md``. No network,
no LLM calls.

Case statuses:

- ``pass``  — every rubric check passed
- ``fail``  — at least one check ran and failed
- ``error`` — the case could not be evaluated (unknown session_ref, or a
  check raised on bad params); harness/config bug, not an agent failure
- ``skip``  — no ``session_ref`` (e.g. live-mode-only cases); reported but
  not graded

Aggregate ``score`` = passed / total (skips and errors count as not passed).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from devin_internals.parsers.sessions import Session, SessionsStore

from devin_evals import __version__
from devin_evals.cases import EvalCase, load_cases
from devin_evals.graders import Evidence, ToolCall, grade_check

_TOOL_NAME_KEYS = ("name", "tool_name", "tool", "kind")


def _tool_name(call_json: str | None) -> str | None:
    if not call_json:
        return None
    try:
        data = json.loads(call_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    for key in _TOOL_NAME_KEYS:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return None


def _message_text(chat_message: str) -> str:
    try:
        data = json.loads(chat_message)
    except json.JSONDecodeError:
        return chat_message
    if isinstance(data, dict):
        # "text" is the CLI shape; "content" is the ACP/dream blob shape.
        for key in ("text", "content"):
            value = data.get(key)
            if isinstance(value, str):
                return value
    return chat_message


def build_evidence(store: SessionsStore, session: Session) -> Evidence:
    """Reduce one recorded session to the grader-facing Evidence shape."""
    texts = [
        _message_text(n.chat_message)
        for n in store.message_nodes(session.id)
    ]
    texts += [p.content for p in store.prompt_history(session.id)]
    calls = tuple(
        ToolCall(
            tool_call_id=tc.tool_call_id,
            name=_tool_name(tc.tool_call_json),
            call_json=tc.tool_call_json,
            update_json=tc.tool_call_update_json,
        )
        for tc in store.tool_call_state(session.id)
    )
    return Evidence(
        session_id=session.id,
        working_directory=session.working_directory,
        transcript="\n".join(texts),
        tool_calls=calls,
    )


def _find_session(store: SessionsStore, ref: str) -> Session | None:
    sessions = store.sessions()
    for s in sessions:
        if s.id == ref:
            return s
    for s in sessions:
        if s.title == ref:
            return s
    return None


def evaluate_case(case: EvalCase, store: SessionsStore) -> dict[str, Any]:
    if not case.session_ref:
        return {
            "id": case.id,
            "description": case.description,
            "session_ref": case.session_ref,
            "source": case.source,
            "status": "skip",
            "score": 0.0,
            "detail": "no session_ref — cannot replay offline",
            "checks": [],
        }
    session = _find_session(store, case.session_ref)
    if session is None:
        return {
            "id": case.id,
            "description": case.description,
            "session_ref": case.session_ref,
            "source": case.source,
            "status": "error",
            "score": 0.0,
            "detail": f"session {case.session_ref!r} not found in sessions.db",
            "checks": [],
        }

    evidence = build_evidence(store, session)
    checks = []
    had_error = False
    for c in case.checks:
        try:
            r = grade_check(c.grader, c.params, evidence)
        except Exception as exc:  # UnknownGraderError shouldn't happen post-load
            r = None
            had_error = True
            checks.append(
                {
                    "grader": c.grader,
                    "params": c.params,
                    "passed": False,
                    "error": True,
                    "detail": f"grader raised: {exc}",
                }
            )
            continue
        had_error = had_error or r.error
        checks.append(
            {
                "grader": r.grader,
                "params": r.params,
                "passed": r.passed,
                "error": r.error,
                "detail": r.detail,
            }
        )

    n_passed = sum(1 for c in checks if c["passed"])
    status = "error" if had_error else ("pass" if n_passed == len(checks) else "fail")
    return {
        "id": case.id,
        "description": case.description,
        "session_ref": case.session_ref,
        "source": case.source,
        "status": status,
        "score": n_passed / len(checks),
        "detail": f"{n_passed}/{len(checks)} checks passed",
        "checks": checks,
    }


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    if s["score"] is None:
        score_line = "- score: no cases"
    else:
        pct = int(round(100 * s["score"]))
        score_line = f"- score: {s['passed']}/{s['total']} cases passed ({pct}%)"
    lines = [
        "# devin-evals report",
        "",
        f"- sessions_db: `{report['sessions_db']}` (schema v{report['schema_version']})",
        f"- evals: `{report['evals_dir']}`",
        score_line,
        "",
        "| case | status | checks | score |",
        "|---|---|---|---|",
    ]
    for c in report["cases"]:
        n = len(c["checks"])
        n_ok = sum(1 for ch in c["checks"] if ch["passed"])
        checks_cell = f"{n_ok}/{n}" if n else "—"
        lines.append(
            f"| {c['id']} | {c['status'].upper()} | {checks_cell} "
            f"| {int(round(100 * c['score']))}% |"
        )
    lines.append("")
    for c in report["cases"]:
        lines.append(f"## {c['id']} — {c['status'].upper()}")
        if c["description"]:
            lines.append(f"{c['description']}")
        if c.get("session_ref"):
            lines.append(f"session: `{c['session_ref']}`")
        lines.append(f"_{c['detail']}_")
        lines.append("")
        for ch in c["checks"]:
            mark = "PASS" if ch["passed"] else ("ERROR" if ch["error"] else "FAIL")
            params = json.dumps(ch["params"], sort_keys=True)
            lines.append(f"- {mark} `{ch['grader']}` `{params}` — {ch['detail']}")
        lines.append("")
    return "\n".join(lines)


def run_evals(
    evals_dir: str | Path,
    sessions_db: str | Path,
    out_dir: str | Path | None = None,
    packs_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Grade every case in ``evals_dir`` against ``sessions_db``.

    Writes ``report.json`` + ``report.md`` under ``out_dir`` when given.
    Returns the report dict (identical content to ``report.json``).
    """
    cases = load_cases(evals_dir, packs_dir=packs_dir)
    case_results: list[dict[str, Any]] = []
    with SessionsStore(sessions_db) as store:
        schema_version = store.schema_info["schema_version"]
        for case in cases:
            case_results.append(evaluate_case(case, store))

    counts = {"pass": 0, "fail": 0, "skip": 0, "error": 0}
    for c in case_results:
        counts[c["status"]] += 1
    total = len(case_results)
    report: dict[str, Any] = {
        "tool": "devin-evals",
        "version": __version__,
        "sessions_db": str(sessions_db),
        "schema_version": schema_version,
        "evals_dir": str(evals_dir),
        "cases": case_results,
        "summary": {
            "total": total,
            "passed": counts["pass"],
            "failed": counts["fail"],
            "skipped": counts["skip"],
            "errored": counts["error"],
            "score": (counts["pass"] / total) if total else None,
        },
    }

    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return report
