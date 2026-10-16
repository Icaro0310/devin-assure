"""copilot-cli provider — GitHub Copilot CLI session stores.

Layout (several coexist): ``<root>/session-state/<uuid>/events.jsonl``
with a sibling ``workspace.yaml``, flat ``<root>/session-state/<uuid>.jsonl``,
and the same two shapes under legacy ``history-session-state/``. Roots
scan in order ``$COPILOT_DIR``, ``$COPILOT_HOME``, ``~/.copilot`` — every
existing root merges. The same native id under a different root is a
duplicate only when the workspace seed (``git_root``/``cwd``) also
matches; the first readable copy wins.

``*.message`` events become messages — text from ``content``/``text``/
``message``/``reasoningText`` or a recursive string walk minus structural
keys (ids, ciphertext blobs, model metadata). ``tool.*`` events become
tool calls: the per-tool payload shapes are not documented, so each
event contributes its ``data`` dict — ``tool.execution_start`` as
``payload`` and ``tool.execution_complete`` as ``update_payload``, paired
by ``toolCallId`` — and the verifier's tolerant kind/command/status
sniffing reads them like any other payload. A ``success`` boolean on a
completion is rewritten to a ``status`` string so exit state resolves.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from devin_qa_pack.evidence import (
    EvidenceMessage,
    EvidenceToolCall,
    SessionEvidence,
    copilot_roots,
    env_or_default,
    epoch_ms,
    iso_from_ms,
)

SURFACE = "copilot-cli"

_STATE_DIRS = ("session-state", "history-session-state")
_MESSAGE_ROLES = {
    "user.message": "user",
    "assistant.message": "assistant",
    "system.message": "system",
}
# Structural keys inside a message event's ``data`` that are metadata or
# opaque wire blobs (``reasoningOpaque``/``encryptedContent`` are
# ciphertext, not text) — skipped so ids/timestamps/models don't pollute
# message text.
_DATA_SKIP = frozenset(
    {
        "id", "parentId", "messageId", "originatingMessageId",
        "interactionId", "turnId", "apiCallId", "toolCallId", "callId",
        "sessionId", "timestamp", "model", "producer", "type", "rte",
        "reasoningOpaque", "encryptedContent", "reasoningBlocks",
        "signature",
    }
)


def _strings(value: Any, skip_keys: frozenset[str] = frozenset()) -> Iterator[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k) in skip_keys:
                continue
            yield from _strings(v, skip_keys)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v, skip_keys)
    elif isinstance(value, str):
        yield value


def _join_strings(value: Any, skip_keys: frozenset[str] = frozenset()) -> str:
    return " ".join(s for s in _strings(value, skip_keys) if s.strip())


def _event_text(data: Any) -> str:
    """Readable text of a ``*.message`` event's ``data`` payload."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("content", "text", "message", "reasoningText"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, (dict, list)):
                text = _join_strings(value, _DATA_SKIP)
                if text:
                    return text
        return _join_strings(data, _DATA_SKIP)
    return _join_strings(data)


def _yaml_scalars(text: str) -> dict[str, str]:
    """Top-level ``key: value`` scalars of a flat YAML file (nested
    blocks, lists and comments ignored — ``workspace.yaml`` is flat)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", line)
        if not m:
            continue
        value = m.group(2).strip()
        if not value or value in ("|", ">", "~", "null"):
            continue
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            try:
                value = str(json.loads(value))
            except json.JSONDecodeError:
                value = value[1:-1]
        elif value.startswith("'") and value.endswith("'") and len(value) >= 2:
            value = value[1:-1].replace("''", "'")
        else:
            value = re.sub(r"\s+#.*$", "", value).strip()
        if value:
            out[m.group(1)] = value
    return out


def _normalize_seed(path: str) -> str:
    seed = path.strip()
    while len(seed) > 1 and seed.endswith(("/", "\\")):
        seed = seed[:-1]
    return seed


def _files(roots: Iterable[Path]) -> list[tuple[Path, Path]]:
    """``(root, events-file)`` pairs across the copilot roots, in scan order."""
    files: list[tuple[Path, Path]] = []
    seen: set[str] = set()
    for root in roots:
        for state_dir in _STATE_DIRS:
            base = root / state_dir
            if not base.is_dir():
                continue
            try:
                entries = sorted(base.iterdir())
            except OSError:
                continue
            for entry in entries:
                path: Path | None = None
                if entry.is_dir():
                    candidate = entry / "events.jsonl"
                    if candidate.is_file():
                        path = candidate
                elif entry.is_file() and entry.name.endswith(".jsonl"):
                    path = entry
                if path is not None and str(path) not in seen:
                    seen.add(str(path))
                    files.append((root, path))
    return files


def _events(path: Path) -> tuple[list[dict[str, Any]], int] | None:
    """The file's JSONL events plus a count of malformed lines
    (``None`` when the file is unreadable)."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    events: list[dict[str, Any]] = []
    bad = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if not isinstance(event, dict):
            bad += 1
            continue
        events.append(event)
    return events, bad


def _call_id(data: dict[str, Any], fallback: str) -> str:
    for key in ("toolCallId", "callId", "call_id", "id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback


def _session_from_events(
    native_id: str,
    path: Path,
    events: list[dict[str, Any]],
    working_dir: str | None,
    warnings: list[str],
) -> SessionEvidence:
    messages: list[EvidenceMessage] = []
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    started: float | None = None
    for i, event in enumerate(events):
        ts = epoch_ms(event.get("timestamp"))
        if event.get("type") == "session.start":
            started = started if started is not None else ts
        role = _MESSAGE_ROLES.get(event.get("type") or "")
        if role is not None:
            text = _event_text(event.get("data"))
            if text.strip():
                messages.append(
                    EvidenceMessage(role=role, text=text, ts_ms=ts)
                )
            continue
        ev_type = str(event.get("type") or "")
        if not ev_type.startswith("tool."):
            continue
        data = event.get("data")
        data = data if isinstance(data, dict) else {}
        cid = _call_id(data, f"event-{i}")
        entry = calls.setdefault(cid, {"ts": ts})
        entry["ts"] = entry["ts"] if entry["ts"] is not None else ts
        if cid not in order:
            order.append(cid)
        if ev_type.endswith((".start", ".begin", ".call")):
            entry["payload"] = data
        elif ev_type.endswith((".complete", ".end", ".finish", ".result",
                              ".output")):
            entry["update"] = data
        elif "payload" not in entry:
            # Unknown tool.* shape — treat as the call itself; the
            # verifier's kind/command sniffing decides what it is.
            entry["payload"] = data
        else:
            entry["update"] = data
    tool_calls: list[EvidenceToolCall] = []
    for cid in order:
        entry = calls[cid]
        payload = entry.get("payload") or {}
        update = entry.get("update")
        kind = ""
        if isinstance(payload.get("toolName"), str):
            kind = payload["toolName"]
        elif isinstance(update, dict) and isinstance(update.get("toolName"), str):
            kind = update["toolName"]
        if isinstance(update, dict):
            update = _normalize_update(update)
        tool_calls.append(
            EvidenceToolCall(
                tool_call_id=cid,
                kind=kind,
                payload=payload,
                update_payload=update,
                ts_ms=entry.get("ts"),
            )
        )
    return SessionEvidence(
        surface=SURFACE,
        native_id=native_id,
        ref=str(path),
        started_at=iso_from_ms(started),
        working_dir=working_dir,
        messages=messages,
        tool_calls=tool_calls,
        warnings=warnings,
    )


def _normalize_update(data: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a completion's ``success`` bool to a ``status`` string so
    the verifier's status sniffing resolves it (not overwritten when a
    real status field exists)."""
    success = data.get("success")
    if isinstance(success, bool) and not isinstance(data.get("status"), str):
        data = {**data, "status": "completed" if success else "failed"}
    return data


class CopilotCliProvider:
    """Copilot CLI session-state roots (env-detected or explicit)."""

    surface = SURFACE

    def __init__(self, roots: Iterable[str | Path] | None = None) -> None:
        self._roots = (
            [Path(r).expanduser() for r in roots] if roots is not None else None
        )

    def _resolve(self, env: Mapping[str, str] | None) -> list[Path]:
        if self._roots is not None:
            return self._roots
        return [r for r in copilot_roots(env_or_default(env)) if r.is_dir()]

    def available(self, env: Mapping[str, str] | None = None) -> bool:
        return bool(self._resolve(env))

    def sessions(
        self, env: Mapping[str, str] | None = None
    ) -> list[SessionEvidence]:
        roots = self._resolve(env)
        out: list[SessionEvidence] = []
        # Dedup: (root, native id) silently, plus the same id under a
        # different root when its workspace seed matches a kept session.
        seen_in_root: set[tuple[str, str]] = set()
        seeds_by_id: dict[str, set[str]] = {}
        for root, path in _files(roots):
            if path.name == "events.jsonl":
                native_id = path.parent.name
                yaml_path = path.parent / "workspace.yaml"
            else:
                native_id = path.stem
                yaml_path = None
            warnings: list[str] = []
            ws: dict[str, str] = {}
            if yaml_path is not None and yaml_path.is_file():
                try:
                    ws = _yaml_scalars(
                        yaml_path.read_text(encoding="utf-8", errors="replace")
                    )
                except OSError:
                    warnings.append("workspace metadata file unreadable")
            working_dir = ws.get("git_root") or ws.get("cwd") or None
            root_key = (str(root), native_id)
            if root_key in seen_in_root:
                continue
            seed = _normalize_seed(working_dir) if working_dir else ""
            if seed and seed in seeds_by_id.get(native_id, set()):
                continue  # same session copied into another root
            scanned = _events(path)
            if scanned is None:
                continue  # unreadable — another root's copy may still read
            events, bad = scanned
            if bad:
                warnings.append(f"{bad} malformed line(s) skipped")
            seen_in_root.add(root_key)
            if seed:
                seeds_by_id.setdefault(native_id, set()).add(seed)
            out.append(
                _session_from_events(native_id, path, events,
                                     working_dir, warnings)
            )
        out.sort(key=lambda s: (s.started_at or "", s.native_id),
                 reverse=True)
        return out


PROVIDER_CLASS = CopilotCliProvider
