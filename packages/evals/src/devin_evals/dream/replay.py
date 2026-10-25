"""dream replay — re-emit a real session as an anonymized synthetic template.

Reads a real ``sessions.db`` through devin-internals' gated, read-only
parser and re-emits the session's *shape* as a synthetic template:
message count, roles and forest linkage, the tool-call sequence and
labels are preserved, while every content string is replaced by a
deterministic ``<kind>_<sha8>`` placeholder keyed by the content hash.
Raw text — prompts, tool output, paths, titles, ids — never reaches the
template, so a replayed corpus cannot leak secrets. Same input db →
same template bytes.

The template round-trips through the generator: :func:`template_to_spec`
rebuilds a :class:`~devin_evals.dream.defects.SessionSpec` that
``write_sessions_db`` materializes as a parseable synthetic
``sessions.db`` (``devin-evals dream replay --emit-db``). One known gap:
``prompt_history``/``rendered_commits``/``subagent_heads`` side tables
are not replayed — the generator has no spec fields for them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from devin_internals.parsers.sessions import Session, SessionsStore

from devin_evals.dream.defects import SessionSpec, ToolCallSpec

FORMAT = "devin-dream/session-template"
FORMAT_VERSION = 1

REPLAY_LABEL = "replay"
REPLAY_DEFECT = "REPLAY"


class ReplayError(RuntimeError):
    """Replay-specific failure (session not found, bad template, ...)."""


# JSON keys whose values are bounded enums — part of the session's shape,
# not user content — so they pass through verbatim. Every other string
# value is treated as content and hashed. Object *keys* are assumed
# structural (field names like "command"/"path"); content values live on
# the right-hand side.
_STRUCTURAL_KEYS = frozenset({"type", "kind", "status", "role", "signal"})

def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _ph(prefix: str, text: str) -> str:
    return f"{prefix}_{_sha8(text)}"


def _anon_json(
    value: Any,
    *,
    prefix: str,
    overrides: dict[str, str] | None = None,
    _key: str | None = None,
) -> Any:
    """Replace every content string with a deterministic placeholder.

    Dict keys, numbers, booleans and nulls are structure and pass through,
    as do values under :data:`_STRUCTURAL_KEYS`. Equal content → equal
    placeholder, so repeated strings (a file path touched by many tool
    calls, a pasted credential) keep their equality pattern without
    leaking the bytes. ``overrides`` maps exact raw strings to their
    placeholder — used so a tool_call_id reads the same inside the blobs
    as in the column.
    """
    if isinstance(value, dict):
        return {
            k: _anon_json(v, prefix=prefix, overrides=overrides, _key=k)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _anon_json(v, prefix=prefix, overrides=overrides)
            for v in value
        ]
    if isinstance(value, str):
        if _key in _STRUCTURAL_KEYS:
            return value
        if overrides and value in overrides:
            return overrides[value]
        return _ph(prefix, value)
    return value


def _anon_blob(
    raw: str | None,
    *,
    prefix: str,
    overrides: dict[str, str] | None = None,
) -> Any:
    """Anonymize one JSON blob column; opaque text degrades to a hash."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"replay_opaque": _ph(prefix, raw)}
    return _anon_json(parsed, prefix=prefix, overrides=overrides)


def _labels_of(session: Session) -> list[str]:
    """Labels recorded in ``sessions.metadata`` (dream/internals convention).

    Labels are a controlled vocabulary and are preserved verbatim —
    the rest of the metadata blob is content and is not copied.
    """
    if not session.metadata:
        return []
    try:
        meta = json.loads(session.metadata)
    except json.JSONDecodeError:
        return []
    labels = meta.get("labels") if isinstance(meta, dict) else None
    if not isinstance(labels, list):
        return []
    return [str(l) for l in labels]


def _workspace_dirs(session: Session) -> list[str]:
    try:
        dirs = json.loads(session.workspace_dirs) \
            if session.workspace_dirs else []
    except json.JSONDecodeError:
        dirs = []
    if not isinstance(dirs, list) or not dirs:
        dirs = [session.working_directory]
    return [str(d) for d in dirs]


def replay_session(store: SessionsStore, session: Session) -> dict[str, Any]:
    """Build the anonymized template for one session row."""
    messages = []
    for n in store.message_nodes(session.id):
        blob = _anon_blob(n.chat_message, prefix="msg")
        role = blob.get("role") if isinstance(blob, dict) else None
        messages.append({
            "node_id": n.node_id,
            "parent_node_id": n.parent_node_id,
            "role": role if isinstance(role, str) else "unknown",
            "chat_message": blob,
        })
    tcs = store.tool_call_state(session.id)
    # tool_call_state has no sequence column — the PK index on
    # (session_id, tool_call_id) decides scan order, so plain hashed ids
    # would reshuffle the sequence when the template is regenerated.
    # Prefixing the scan index keeps alphabetical order == scan order.
    id_map = {
        tc.tool_call_id: f"tc_{i + 1:04d}_{_sha8(tc.tool_call_id)}"
        for i, tc in enumerate(tcs)
    }
    tool_calls = [{
        "tool_call_id": id_map[tc.tool_call_id],
        "call": _anon_blob(tc.tool_call_json, prefix="str",
                           overrides=id_map),
        "update": _anon_blob(tc.tool_call_update_json, prefix="str",
                             overrides=id_map),
    } for tc in tcs]
    return {
        "session_id": _ph("sess", session.id),
        "title": _ph("title", session.title or ""),
        "working_directory": _ph("dir", session.working_directory),
        "workspace_dirs": [_ph("dir", d) for d in _workspace_dirs(session)],
        "model": _ph("model", session.model),
        "backend_type": _ph("backend", session.backend_type),
        "agent_mode": session.agent_mode,
        "hidden": session.hidden,
        "duration_ms": max(0, session.last_activity_at - session.created_at),
        "labels": _labels_of(session),
        "messages": messages,
        "tool_calls": tool_calls,
        "stats": {
            "message_count": len(messages),
            "tool_call_count": len(tool_calls),
            "schema_version": store.schema_info["schema_version"],
        },
    }


def replay_db(db_path: str | Path,
              session_id: str | None = None) -> dict[str, Any]:
    """Read a real ``sessions.db`` and emit the anonymized template envelope.

    Raises:
        SchemaDetectionError: file missing or not a sessions.db.
        UnknownSchemaVersionError: schema drifted past the known range.
        SchemaError: known but unsupported schema version.
        ReplayError: ``session_id`` not present in the db.
        sqlite3.DatabaseError: corrupt database file.
    """
    with SessionsStore(db_path) as store:
        sessions = sorted(store.sessions(), key=lambda s: s.id)
        if session_id is not None:
            matched = [s for s in sessions if s.id == session_id]
            if not matched:
                raise ReplayError(
                    f"{db_path}: session {session_id!r} not found "
                    f"({len(sessions)} session(s) present)")
            sessions = matched
        return {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "generator": "devin-evals dream replay",
            "source": {
                "schema_version": store.schema_info["schema_version"],
                "session_count": len(sessions),
                "anonymized": True,
            },
            "sessions": [replay_session(store, s) for s in sessions],
        }


def dumps_template(envelope: dict[str, Any]) -> str:
    """Canonical serialization — sorted keys, so bytes are deterministic."""
    return json.dumps(envelope, indent=2, sort_keys=True) + "\n"


def template_to_spec(template: dict[str, Any]) -> SessionSpec:
    """Rebuild a :class:`SessionSpec` from one anonymized template entry.

    ``node_id``/``parent_node_id`` are translated to positions in the
    regenerated chain so the message forest survives materialization; a
    parent that dangles (or the node has none) becomes ``None``.
    """
    messages = template.get("messages", [])
    pos = {m.get("node_id"): i + 1 for i, m in enumerate(messages)}
    spec_msgs = []
    parents = []
    for m in messages:
        role = m.get("role")
        spec_msgs.append((
            role if isinstance(role, str) else "unknown",
            json.dumps(m.get("chat_message"), sort_keys=True),
        ))
        parents.append(pos.get(m.get("parent_node_id")))
    tool_calls = tuple(
        ToolCallSpec(
            tool_call_id=str(tc.get("tool_call_id", f"tc_{i:04d}")),
            call=tc.get("call"),
            update=tc.get("update"),
        )
        for i, tc in enumerate(template.get("tool_calls", []))
    )
    labels = ["synthetic", REPLAY_LABEL]
    for l in template.get("labels", []):
        if l not in labels:
            labels.append(str(l))
    stats = template.get("stats", {})
    return SessionSpec(
        defect_id=REPLAY_DEFECT,
        session_id=str(template.get("session_id", "sess_replay")),
        title=str(template.get("title", "title_replay")),
        working_directory=str(
            template.get("working_directory", "dir_replay")),
        model=str(template.get("model", "model_replay")),
        agent_mode=str(template.get("agent_mode", "interactive")),
        messages=tuple(spec_msgs),
        message_parents=tuple(parents),
        tool_calls=tool_calls,
        labels=tuple(labels),
        workspace_dirs=tuple(template.get("workspace_dirs") or ()),
        duration_ms=int(template.get("duration_ms") or 120_000),
        expected={
            "replay": True,
            "message_count": stats.get("message_count", len(spec_msgs)),
            "tool_call_count": stats.get("tool_call_count", len(tool_calls)),
        },
    )


def envelope_to_specs(envelope: dict[str, Any]) -> list[SessionSpec]:
    """Validate a template envelope and rebuild every session spec."""
    if envelope.get("format") != FORMAT:
        raise ReplayError(
            f"unknown template format {envelope.get('format')!r} "
            f"(expected {FORMAT!r})")
    if envelope.get("format_version") != FORMAT_VERSION:
        raise ReplayError(
            f"unknown template format_version "
            f"{envelope.get('format_version')!r} "
            f"(expected {FORMAT_VERSION})")
    return [template_to_spec(s) for s in envelope.get("sessions", [])]
