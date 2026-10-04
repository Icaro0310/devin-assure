"""Live audit at SessionEnd (QA-1).

Audits exactly one session — the one that just ended — and writes the
verdict to a *side file*, never into the transcript or any Devin store.
Default path: ``<data-dir>/qa/<session-id>.json`` (``--out`` overrides;
``--data-dir`` overrides the auto-detected Devin data dir). The file
carries ``{session_id, verdict, claims: [...], audited_at}`` — plus an
optional ``intent`` field (QA-4 prompt-vs-touched-paths analysis) when
the session could be resolved — and the command also prints a one-line
summary.

Session resolution order: ``--session-id`` → the hook payload on stdin
(``{"session_id": "..."}``, what the hook dispatcher pipes in) → the
``DEVIN_SESSION_ID`` env var → the most recently active session in
``sessions.db``.

Fail-soft contract: once the command runs it always exits ``0``. A
session that cannot be resolved or audited yields a ``SKIPPED`` verdict
(written to the side file when a session id is known) instead of an
error, so a SessionEnd hook can never fail the host session. Non-zero
exits are reserved for usage errors (argparse → ``2``), consistent with
the other subcommands.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from devin_internals.parsers import SessionsStore
from devin_internals.parsers.sessions import Session
from devin_internals.schema import SchemaError

from devin_qa_pack.intent import audit_intent, intent_dict
from devin_qa_pack.paths import default_data_dir, default_sessions_db
from devin_qa_pack.report import audit_dict, audit_session

SKIPPED = "SKIPPED"

_SESSION_ID_KEYS = ("session_id", "sessionId", "session")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _safe_filename(session_id: str) -> str:
    return _SAFE_NAME_RE.sub("_", session_id) or "session"


def default_out_path(
    session_id: str, data_dir: str | Path | None = None
) -> Path:
    """``<data-dir>/qa/<session-id>.json`` — the side-file location."""
    root = Path(data_dir).expanduser() if data_dir else default_data_dir()
    return root / "qa" / f"{_safe_filename(session_id)}.json"


def session_id_from_payload(payload: Any) -> str | None:
    """Session id inside a hook payload dict (several key spellings)."""
    if not isinstance(payload, dict):
        return None
    for key in _SESSION_ID_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def session_id_from_stdin(stream: IO[str] | None = None) -> str | None:
    """Read the hook payload JSON from ``stream`` (default ``sys.stdin``).

    Returns ``None`` on a tty (human running the command — no payload),
    on empty input, on read errors, or on non-JSON input.
    """
    stream = sys.stdin if stream is None else stream
    try:
        isatty = getattr(stream, "isatty", None)
        if callable(isatty) and isatty():
            return None
        raw = stream.read()
    except (OSError, ValueError):
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return session_id_from_payload(json.loads(raw))
    except json.JSONDecodeError:
        return None


def resolve_session_id(
    explicit: str | None,
    stdin_stream: IO[str] | None = None,
    env: dict[str, str] | None = None,
) -> str | None:
    """``--session-id`` → stdin hook payload → ``$DEVIN_SESSION_ID``."""
    if explicit:
        return explicit
    sid = session_id_from_stdin(stdin_stream)
    if sid:
        return sid
    env = os.environ if env is None else env
    sid = env.get("DEVIN_SESSION_ID")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def _resolve_session(
    store: SessionsStore, session_id: str | None
) -> tuple[Session | None, str | None]:
    """``(session, None)`` or ``(None, skip-reason)``.

    No id → the most recently active session; a prefix must be unique.
    """
    if not session_id:
        latest = store.sessions(limit=1)
        if not latest:
            return None, "no sessions in store"
        return latest[0], None
    sessions = store.sessions()
    for s in sessions:
        if s.id == session_id:
            return s, None
    matches = [s for s in sessions if s.id.startswith(session_id)]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return (
            None,
            f"session prefix '{session_id}' is ambiguous "
            f"({len(matches)} matches)",
        )
    return None, f"unknown session '{session_id}'"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomic-ish side-file write: tmp file + replace, parents created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


@dataclass
class SessionEndResult:
    session_id: str | None
    verdict: str  # PASS | PARTIAL | UNVERIFIED | SKIPPED
    payload: dict[str, Any]
    out_path: Path | None
    reason: str | None = None


def summary_line(result: SessionEndResult) -> str:
    """The one-line summary printed to stdout."""
    sid = result.session_id or "-"
    out = f" → {result.out_path}" if result.out_path else ""
    if result.verdict == SKIPPED:
        return f"qa session-end: SKIPPED {sid} — {result.reason}{out}"
    counts = result.payload.get("summary", {})
    n = len(result.payload.get("claims", []))
    return (
        f"qa session-end: {result.verdict} {sid} — {n} claim(s): "
        f"{counts.get('verified', 0)} verified, "
        f"{counts.get('disputed', 0)} disputed, "
        f"{counts.get('unverifiable', 0)} unverifiable{out}"
    )


def run_session_end(
    *,
    sessions_db: str | Path | None = None,
    session_id: str | None = None,
    data_dir: str | Path | None = None,
    out: str | Path | None = None,
    claim_limit: int | None = None,
    stdin_stream: IO[str] | None = None,
    env: dict[str, str] | None = None,
) -> SessionEndResult:
    """Audit the session that just ended; write the side file.

    Never raises for store/session problems — those produce a ``SKIPPED``
    result. May still raise on unexpected internal errors (the CLI wraps
    this and still exits 0).
    """
    sid = resolve_session_id(session_id, stdin_stream, env)
    out_path = (
        Path(out).expanduser()
        if out
        else (default_out_path(sid, data_dir) if sid else None)
    )

    audit = None
    intent = None
    reason: str | None = None
    db = (
        Path(sessions_db).expanduser()
        if sessions_db
        else default_sessions_db()
    )
    if db is None or not db.is_file():
        reason = f"sessions.db not found: {db}"
    else:
        try:
            with SessionsStore(db) as store:
                session, reason = _resolve_session(store, sid)
                if session is not None:
                    audit = audit_session(
                        store, session, claim_limit=claim_limit
                    )
                    try:
                        intent = audit_intent(
                            session,
                            store.message_nodes(session.id),
                            store.tool_call_state(session.id),
                        )
                    except Exception:
                        intent = None  # fail-soft — side field is optional
        except (SchemaError, OSError) as exc:
            reason = f"cannot open {db}: {exc}"

    if audit is not None:
        payload = audit_dict(audit)
        if intent is not None:
            payload["intent"] = intent_dict(intent)
        payload["audited_at"] = _utc_now()
        result = SessionEndResult(
            session_id=audit.session_id,
            verdict=audit.verdict,
            payload=payload,
            out_path=out_path,
        )
    else:
        result = SessionEndResult(
            session_id=sid,
            verdict=SKIPPED,
            payload={
                "session_id": sid,
                "verdict": SKIPPED,
                "reason": reason,
                "claims": [],
                "audited_at": _utc_now(),
            },
            out_path=out_path,
            reason=reason,
        )

    if result.out_path is not None:
        _write_json(result.out_path, result.payload)
    return result
