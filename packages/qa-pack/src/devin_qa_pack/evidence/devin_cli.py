"""devin-cli provider — Devin CLI ``sessions.db`` via ``SessionsStore``.

``message_nodes`` rows decode into :class:`EvidenceMessage` — the
``chat_message`` payload decode that used to live in claims.py lives
here, so claim extraction gets plain role/text. ``tool_call_state``
rows decode into :class:`EvidenceToolCall`; a row whose payloads both
fail to decode yields an empty-payload call — still counted, so verify
treats it as unreadable ground truth rather than absent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from devin_internals.parsers import SessionsStore
from devin_internals.parsers.sessions import Session

from devin_qa_pack.evidence import (
    EvidenceMessage,
    EvidenceToolCall,
    SessionEvidence,
    env_or_default,
    epoch_ms,
    iso_from_ms,
    normalize_role,
)
from devin_qa_pack.paths import default_sessions_db

SURFACE = "devin-cli"


def _decode_message(chat_message: str) -> tuple[str | None, str]:
    """Return ``(role, text)``. Non-JSON payloads become ``(None, raw)``.

    ``chat_message`` payloads are marked *unstable* in
    devin-internals-spec SCHEMA.md, so decoding is defensive: JSON dicts
    are checked for the usual role/text/content keys (including
    content-block lists); anything that is not JSON at all stays plain
    text.
    """
    try:
        data = json.loads(chat_message)
    except (json.JSONDecodeError, TypeError):
        return None, chat_message
    if isinstance(data, str):
        return None, data
    if not isinstance(data, dict):
        return None, ""
    role = data.get("role") or data.get("author") or data.get("sender")
    return (str(role).lower() if role is not None else None), _text_of(data)


def _text_of(data: dict[str, Any]) -> str:
    for key in ("text", "content", "message", "body", "markdown"):
        value = data.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [
                block.get("text", "")
                for block in value
                if isinstance(block, dict)
            ]
            text = "\n".join(p for p in parts if p)
            if text:
                return text
    return ""


def _json_dict(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _sniff_kind(payloads: list[dict[str, Any] | None]) -> str:
    """Informational kind: the first ``kind``/``type``/``tool`` string."""
    for payload in payloads:
        if isinstance(payload, dict):
            raw = payload.get("kind") or payload.get("type") or payload.get("tool")
            if isinstance(raw, str) and raw:
                return raw.lower()
    return ""


def session_evidence(
    store: SessionsStore, session: Session, *, ref: str = ""
) -> SessionEvidence:
    """One session's rows as :class:`SessionEvidence`."""
    messages = []
    for node in store.message_nodes(session.id):
        role, text = _decode_message(node.chat_message)
        messages.append(
            EvidenceMessage(
                role=normalize_role(role),
                text=text,
                ts_ms=epoch_ms(node.created_at),
                source_id=node.node_id,
            )
        )
    tool_calls = []
    for state in store.tool_call_state(session.id):
        payload = _json_dict(state.tool_call_json)
        update = _json_dict(state.tool_call_update_json)
        tool_calls.append(
            EvidenceToolCall(
                tool_call_id=state.tool_call_id,
                kind=_sniff_kind([payload, update]),
                payload=payload or {},
                update_payload=update,
                ts_ms=None,
            )
        )
    return SessionEvidence(
        surface=SURFACE,
        native_id=session.id,
        ref=ref,
        started_at=iso_from_ms(epoch_ms(session.created_at)),
        working_dir=session.working_directory or None,
        messages=messages,
        tool_calls=tool_calls,
    )


class DevinCliProvider:
    """The Devin CLI store — ``--sessions-db`` or auto-detected."""

    surface = SURFACE

    def __init__(self, sessions_db: str | Path | None = None) -> None:
        self._db = Path(sessions_db).expanduser() if sessions_db else None

    def _resolve(self, env: Mapping[str, str] | None) -> Path | None:
        if self._db is not None:
            return self._db
        return default_sessions_db(environ=env_or_default(env))

    def available(self, env: Mapping[str, str] | None = None) -> bool:
        db = self._resolve(env)
        return db is not None and db.is_file()

    def sessions(
        self, env: Mapping[str, str] | None = None
    ) -> list[SessionEvidence]:
        db = self._resolve(env)
        if db is None or not db.is_file():
            return []
        out = []
        with SessionsStore(db) as store:
            for session in store.sessions():
                out.append(session_evidence(store, session, ref=str(db)))
        return out


PROVIDER_CLASS = DevinCliProvider
