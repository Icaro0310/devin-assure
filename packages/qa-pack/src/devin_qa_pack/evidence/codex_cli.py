"""codex-cli provider — OpenAI Codex CLI ``~/.codex`` stores.

Sources: ``state_<N>.sqlite`` (largest N wins; read-only ``threads``
rows carry metadata plus ``first_user_message``) and ``rollout-*.jsonl``
under ``sessions/``, ``archived_sessions/`` and ``$CODEX_SESSIONS_DIR``
(the real transcript: ``session_meta`` + ``response_item``/``event_msg``
records). Forked rollouts (``forked_from_id``) are skipped. A rollout
and a thread row sharing an id merge — the rollout supplies the
transcript, the thread fills missing cwd/start metadata.

Record mapping: ``*.message``/``*_message``/reasoning payloads become
messages; exec-style records (``function_call``/``custom_tool_call``/
``local_shell_call``/``exec_command_begin`` …) become tool calls paired
with their ``*_output``/``exec_command_end`` records by ``call_id``.
``payload = the record dict``, lightly normalized where the wire format
hides ground truth: argv ``command`` lists are joined to strings and
JSON-string ``arguments``/``output`` fields are decoded so nested
``exit_code``/``command`` keys reach the verifier.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from devin_qa_pack.evidence import (
    EvidenceMessage,
    EvidenceToolCall,
    SessionEvidence,
    codex_root,
    connect_readonly,
    env_or_default,
    epoch_ms,
    iso_from_ms,
    normalize_role,
)

SURFACE = "codex-cli"

_STATE_RE = re.compile(r"^state_(\d+)\.sqlite$")
_ROLLOUT_DEPTH = 8
_ROLLOUT_DIRS = ("sessions", "archived_sessions")

# ``payload.type`` → call position: "begin" feeds payload, "end" feeds
# update_payload, "msg" feeds messages (role by payload role field).
_CALL_TYPES = {
    "function_call": "begin",
    "custom_tool_call": "begin",
    "local_shell_call": "begin",
    "exec": "begin",
    "exec_command_begin": "begin",
    "function_call_output": "end",
    "custom_tool_call_output": "end",
    "local_shell_call_output": "end",
    "exec_command_end": "end",
    "exec_command_output": "end",
}
_MESSAGE_TYPES = {
    "user_message": "user",
    "agent_message": "assistant",
    "agent_reasoning": "assistant",
    "reasoning": "assistant",
}
_CONTENT_KEYS = (
    "message", "text", "content", "summary", "input",
)
_PAYLOAD_SKIP = frozenset(
    {"type", "id", "call_id", "timestamp", "role", "status",
     "encrypted_content"}
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


def _record_text(payload: dict[str, Any]) -> str:
    """Text of one ``response_item``/``event_msg`` payload — known
    content keys first, recursive walk minus structural keys last."""
    for key in _CONTENT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (dict, list)):
            text = _join_strings(value, _PAYLOAD_SKIP)
            if text:
                return text
    return _join_strings(payload, _PAYLOAD_SKIP)


def _argv(node: Any) -> Any:
    """Join argv-style ``command``/``cmd`` string lists to shell text."""
    if isinstance(node, dict):
        return {
            k: (" ".join(v) if k.lower() in ("command", "cmd")
                and isinstance(v, list)
                and all(isinstance(x, str) for x in v)
                else _argv(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_argv(v) for v in node]
    return node


def _json_dict_value(value: Any) -> Any:
    """Decode a JSON-string field to its dict; anything else passes."""
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(data, dict):
            return data
    return value


# Rollout ``payload.type`` values that are unambiguously terminal runs —
# normalized to ``"execute"`` so the verifier's kind sniffing sees them
# (an opaque ``local_shell_call`` would otherwise read as "other").
_EXEC_RECORD_TYPES = frozenset(
    {"local_shell_call", "exec", "exec_command", "exec_command_begin"}
)
# ``function_call`` is exec-like only when the callee is a shell tool.
_EXEC_TOOL_NAMES = frozenset(
    {"shell", "exec", "exec_command", "local_shell", "bash", "run",
     "run_in_terminal", "terminal", "container.exec", "command"}
)


def _normalize_call(payload: dict[str, Any]) -> dict[str, Any]:
    """Decode ``arguments``/``action`` containers, join argv commands and
    normalize exec-typed records to ``type: "execute"`` — the verifier's
    tolerant kind sniffing reads these like any other tool call."""
    out = dict(payload)
    for key in ("arguments", "action", "input"):
        value = _json_dict_value(out.get(key))
        if isinstance(value, dict):
            out[key] = value
    out = _argv(out)
    ptype = str(out.get("type") or "")
    if ptype in _EXEC_RECORD_TYPES or (
        ptype in ("function_call", "custom_tool_call")
        and str(out.get("name") or "").lower() in _EXEC_TOOL_NAMES
    ):
        out.setdefault("record_type", ptype)
        out["type"] = "execute"
    # Lift a container's command to the top level — ``action`` is not one
    # of the verifier's container keys, so without this the command only
    # reaches search_text, never the command list a tests/push claim
    # needs.
    command = out.get("command") or out.get("cmd")
    if not isinstance(command, str) or not command.strip():
        for key in ("action", "arguments", "input", "args"):
            inner = out.get(key)
            if isinstance(inner, dict):
                inner_cmd = inner.get("command") or inner.get("cmd")
                if isinstance(inner_cmd, str) and inner_cmd.strip():
                    out["command"] = inner_cmd
                    break
    return out


_EXIT_STATUS_RE = re.compile(
    r"exit(?:ed)?(?:[_ ]?with)?[_ ]?code[:=]?\s*(-?\d+)\s*", re.IGNORECASE
)


def _sniff_exit_text(text: str) -> int | None:
    """Exit code from a plain-text output: Codex appends the status as the
    output's LAST line (``stdout...\nExit code: 0``). Only a full-match on
    the last line — or a bare-integer whole output — counts: an ``exit
    code: 0`` substring *inside* real stdout (a failed run can print one)
    must stay unverifiable, never read as success."""
    stripped = text.strip()
    if re.fullmatch(r"-?\d+", stripped):
        return int(stripped)
    if stripped:
        m = _EXIT_STATUS_RE.fullmatch(stripped.rsplit("\n", 1)[-1])
        if m is not None:
            return int(m.group(1))
    return None


def _normalize_output(payload: dict[str, Any]) -> dict[str, Any]:
    """Decode a JSON-string ``output`` and lift a recorded exit code to
    the top level where the verifier's status sniffing reads it.

    A JSON ``metadata.exit_code`` wins when present; plain-text outputs
    (``"Exit code: 1"``, a bare ``"0"``) are sniffed so shell calls keep
    a verifiable status instead of reading as unknown.
    """
    out = dict(payload)
    value = _json_dict_value(out.get("output"))
    if isinstance(value, dict):
        out["output"] = value
        meta = value.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("exit_code"), int):
            out.setdefault("exit_code", meta["exit_code"])
        if isinstance(value.get("exit_code"), int):
            out.setdefault("exit_code", value["exit_code"])
    elif isinstance(out.get("output"), str):
        code = _sniff_exit_text(out["output"])
        if code is not None:
            out.setdefault("exit_code", code)
    for key in ("exit_code", "exitCode"):
        code = out.get(key)
        if isinstance(code, int) and not isinstance(code, bool):
            out["exit_code"] = code
    return out


def _walk_rollouts(base: Path, out: list[Path], depth: int = 0) -> None:
    if depth > _ROLLOUT_DEPTH:
        return
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir():
            _walk_rollouts(entry, out, depth + 1)
        elif entry.is_file() and entry.name.startswith("rollout-") and (
            entry.name.endswith(".jsonl")
        ):
            out.append(entry)


def _rollout_evidence(path: Path) -> SessionEvidence | None:
    """One rollout file as evidence; ``None`` when unreadable or forked."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    native_id = ""
    working_dir: str | None = None
    started: float | None = None
    messages: list[EvidenceMessage] = []
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    warnings: list[str] = []
    bad = 0
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if not isinstance(record, dict):
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        ts = epoch_ms(record.get("timestamp") or payload.get("timestamp"))
        if record.get("type") == "session_meta":
            if payload.get("forked_from_id"):
                return None  # forks are ignored
            native_id = payload.get("id") or native_id
            git = payload.get("git")
            git_root = (
                (git.get("root") or git.get("repository_root"))
                if isinstance(git, dict) else None
            )
            working_dir = working_dir or payload.get("cwd") or git_root
            started = started or (
                epoch_ms(payload.get("timestamp"))
                or epoch_ms(record.get("timestamp"))
            )
            continue
        if record.get("type") not in ("response_item", "event_msg"):
            continue
        ptype = payload.get("type")
        position = _CALL_TYPES.get(str(ptype))
        if position is not None:
            cid = payload.get("call_id") or payload.get("id") or f"record-{i}"
            cid = str(cid)
            entry = calls.setdefault(cid, {})
            entry["ts"] = entry.get("ts") or ts
            if cid not in order:
                order.append(cid)
            if position == "begin":
                entry["payload"] = _normalize_call(payload)
            else:
                entry["update"] = _normalize_output(payload)
            continue
        role = _MESSAGE_TYPES.get(str(ptype))
        if role is None and ptype == "message":
            role = normalize_role(payload.get("role"))
        if role is not None:
            text = _record_text(payload)
            if text.strip():
                messages.append(
                    EvidenceMessage(role=role, text=text, ts_ms=ts)
                )
    if bad:
        warnings.append(f"{bad} malformed record(s) skipped")
    if not native_id:
        native_id = path.stem  # rollout-<ts>-<uuid> — filename identity
    tool_calls = [
        EvidenceToolCall(
            tool_call_id=cid,
            kind=str((calls[cid].get("payload") or {}).get("name")
                     or (calls[cid].get("payload") or {}).get("type")
                     or ""),
            payload=calls[cid].get("payload") or {},
            update_payload=calls[cid].get("update"),
            ts_ms=calls[cid].get("ts"),
        )
        for cid in order
    ]
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


def _thread_rows(path: Path) -> list[dict[str, Any]]:
    """``threads`` rows of one state db (``[]`` on any read failure)."""
    try:
        con = connect_readonly(path)
    except sqlite3.Error:
        return []
    try:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "threads" not in tables:
            return []
        return [dict(r) for r in con.execute("SELECT * FROM threads")]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _thread_evidence(row: dict[str, Any], ref: str) -> SessionEvidence | None:
    """One ``threads`` row — metadata plus ``first_user_message`` (the
    only row field that is honestly user-authored; ``title``/``preview``/
    ``summary`` are auto-derived and stay out of the evidence)."""
    native_id = row.get("id")
    if not isinstance(native_id, str) or not native_id:
        return None
    messages = []
    first = row.get("first_user_message")
    if isinstance(first, str) and first.strip():
        messages.append(
            EvidenceMessage(
                role="user", text=first,
                ts_ms=epoch_ms(row.get("created_at")),
            )
        )
    cwd = row.get("cwd")
    return SessionEvidence(
        surface=SURFACE,
        native_id=native_id,
        ref=ref,
        started_at=iso_from_ms(epoch_ms(row.get("created_at"))),
        working_dir=cwd if isinstance(cwd, str) and cwd else None,
        messages=messages,
    )


class CodexCliProvider:
    """The Codex home (env-detected or explicit ``root``)."""

    surface = SURFACE

    def __init__(
        self,
        root: str | Path | None = None,
        sessions_dirs: Iterable[str | Path] | None = None,
    ) -> None:
        self._root = Path(root).expanduser() if root else None
        self._extra = (
            [Path(d).expanduser() for d in sessions_dirs]
            if sessions_dirs is not None
            else None
        )

    def _resolve(self, env: dict[str, str]) -> tuple[Path, list[Path]]:
        root = self._root if self._root is not None else codex_root(env)
        extra = list(self._extra or [])
        if self._extra is None and env.get("CODEX_SESSIONS_DIR"):
            extra.append(Path(env["CODEX_SESSIONS_DIR"]))
        return root, extra

    def available(self, env: Mapping[str, str] | None = None) -> bool:
        e = env_or_default(env)
        root, extra = self._resolve(e)
        return root.is_dir() or any(d.is_dir() for d in extra)

    def sessions(
        self, env: Mapping[str, str] | None = None
    ) -> list[SessionEvidence]:
        e = env_or_default(env)
        root, extra = self._resolve(e)
        by_id: dict[str, SessionEvidence] = {}

        def _merge(ev: SessionEvidence) -> None:
            prior = by_id.get(ev.native_id)
            if prior is None:
                by_id[ev.native_id] = ev
            else:
                # Same native id (thread row + rollout): the first keeps
                # its content; the second fills missing metadata only.
                if not prior.working_dir:
                    prior.working_dir = ev.working_dir
                if not prior.started_at:
                    prior.started_at = ev.started_at
                prior.warnings.extend(w for w in ev.warnings)

        rollouts: list[Path] = []
        for base in (root / d for d in _ROLLOUT_DIRS):
            _walk_rollouts(base, rollouts)
        for base in extra:
            _walk_rollouts(base, rollouts)
        # Rollouts first — they carry the transcript; a same-id thread
        # row afterwards only fills missing cwd/start metadata.
        for path in sorted(rollouts):
            ev = _rollout_evidence(path)
            if ev is not None:
                _merge(ev)

        # state_<N>.sqlite — largest N wins.
        state: Path | None = None
        best_n = -1
        try:
            entries = sorted(root.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            m = _STATE_RE.match(entry.name)
            if entry.is_file() and m and int(m.group(1)) > best_n:
                best_n, state = int(m.group(1)), entry
        if state is not None:
            for row in _thread_rows(state):
                ev = _thread_evidence(row, str(state))
                if ev is not None:
                    _merge(ev)

        out = list(by_id.values())
        out.sort(key=lambda s: (s.started_at or "", s.native_id),
                 reverse=True)
        return out


PROVIDER_CLASS = CodexCliProvider
