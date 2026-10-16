"""vscode-chat provider — VS Code Copilot Chat session stores.

Layout (variants may coexist): ``<user>/workspaceStorage/<wsDir>/
chatSessions/<id>.{json,jsonl}`` plus ``<user>/globalStorage/
{emptyWindowChatSessions,transferredChatSessions}/<id>.{json,jsonl}``.
``<user>`` is ``<config>/<Variant>/User`` for ``Code`` and
``Code - Insiders``, plus ``~/.vscode-server/data/User``. A ``.json``
file is a session snapshot; a ``.jsonl`` is a mutation log replayed in
order (kind 0 replace · 1 set · 2 append — truncating to ``i`` first ·
3 delete). For one id the ``.jsonl`` wins.

``<wsDir>`` is VS Code's stable workspace id — a sibling
``workspace.json`` ``folder`` URI wins when present (it carries the
real path); otherwise the URL-decoded dir name is used only when it
still looks like a path — a bare hash id resolves to ``None`` +
warning, never a fake path. globalStorage sessions get no working dir.

``requests[].message`` text becomes user messages, ``requests[].
response`` part text becomes assistant messages, and response parts
carrying tool-invocation fields (``toolCallId``/``toolId``/
``toolInvocation`` kinds) become tool calls — the per-part shapes are
not documented, so the part dict is the payload and a terminal-looking
command string is sniffed into ``command`` for the verifier.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from devin_qa_pack.evidence import (
    EvidenceMessage,
    EvidenceToolCall,
    SessionEvidence,
    env_or_default,
    epoch_ms,
    iso_from_ms,
    vscode_user_dirs,
)

SURFACE = "vscode-chat"

_GLOBAL_DIRS = ("emptyWindowChatSessions", "transferredChatSessions")
# Document keys that mark a recognizable VS Code chat session document.
_KNOWN_KEYS = frozenset(
    {
        "requests", "customTitle", "title", "creationDate",
        "lastMessageDate", "version", "requesterUsername",
        "responderUsername", "sessionId", "initialLocation",
        "isImported", "agent",
    }
)
# Mutation-log path components that must never be followed
# (prototype-pollution guard).
_UNSAFE_KEYS = frozenset({"__proto__", "prototype", "constructor"})
# Readable-content keys of a chat request/response part.
_CONTENT_KEYS = ("text", "value", "content", "parts")
# Response-part kinds that are tool invocations rather than prose.
_TOOL_PART_MARKERS = ("tool", "invocation")
# Payload keys whose string value may be a terminal command.
_COMMAND_KEYS = ("command", "cmd", "commandLine", "script",
                 "terminalCommand", "input", "original")


def _field_text(node: Any) -> str:
    """Text under a chat content field: strings pass through and
    containers are walked through the content keys only, so metadata
    (agent ids, ranges, extension info) never reaches message text."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(
            t for t in (_field_text(x) for x in node) if t
        )
    if isinstance(node, dict):
        return "\n".join(
            t
            for key in _CONTENT_KEYS
            if (t := _field_text(node.get(key)))
        )
    return ""


def _container(root: Any, keys: list[Any]) -> Any:
    """The container at ``keys`` in ``root``, creating dicts/lists
    along the way (mutation replay ``walk``)."""
    cur = root
    for i, key in enumerate(keys):
        child_is_list = i + 1 < len(keys) and (
            isinstance(keys[i + 1], int) and not isinstance(keys[i + 1], bool)
        )
        if isinstance(cur, dict):
            nxt = cur.get(key)
            if not isinstance(nxt, (dict, list)):
                nxt = [] if child_is_list else {}
                cur[key] = nxt
        elif isinstance(cur, list):
            if not isinstance(key, int) or isinstance(key, bool) or key < 0:
                return None
            while len(cur) <= key:
                cur.append(None)
            nxt = cur[key]
            if not isinstance(nxt, (dict, list)):
                nxt = [] if child_is_list else {}
                cur[key] = nxt
        else:
            return None
        cur = nxt
    return cur


def _rebuild(text: str) -> tuple[dict[str, Any], int]:
    """Rebuild a session document: a ``.json`` snapshot, or a ``.jsonl``
    mutation log replayed in order (kind 0 replace · 1 set · 2 append,
    truncating to ``i`` first · 3 delete; unknown kinds skipped).

    Returns ``(doc, bad_lines)`` — malformed lines are skipped and
    counted."""
    trimmed = text.strip()
    if trimmed.startswith("{"):
        try:
            whole = json.loads(trimmed)
        except json.JSONDecodeError:
            whole = None
        if isinstance(whole, dict) and not isinstance(whole.get("kind"), int):
            return whole, 0
    state: dict[str, Any] = {}
    bad_lines = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            bad_lines += 1  # truncated tail or malformed mid-line
            continue
        if not isinstance(record, dict):
            continue
        raw_keys = record.get("k")
        keys = (
            [
                p
                for p in raw_keys
                if isinstance(p, (str, int)) and not isinstance(p, bool)
            ]
            if isinstance(raw_keys, list)
            else []
        )
        if any(isinstance(p, str) and p in _UNSAFE_KEYS for p in keys):
            continue
        last = keys[-1] if keys else None
        kind = record.get("kind")
        if kind == 0:
            if isinstance(record.get("v"), dict):
                state = record["v"]
        elif kind == 1:
            if not keys:
                if isinstance(record.get("v"), dict):
                    state = record["v"]
            else:
                parent = _container(state, keys[:-1])
                if isinstance(parent, dict):
                    parent[last] = record.get("v")
                elif isinstance(parent, list):
                    if not isinstance(last, int) or isinstance(last, bool) or (
                        last < 0
                    ):
                        continue
                    while len(parent) <= last:
                        parent.append(None)
                    parent[last] = record.get("v")
        elif kind == 2:
            if not keys:
                continue
            parent = _container(state, keys[:-1])
            if not isinstance(parent, (dict, list)):
                continue
            arr = parent.get(last) if isinstance(parent, dict) else None
            if isinstance(parent, list):
                if not isinstance(last, int) or isinstance(last, bool) or last < 0:
                    continue
                while len(parent) <= last:
                    parent.append(None)
                arr = parent[last]
            if not isinstance(arr, list):
                arr = []
                parent[last] = arr
            value = record.get("v")
            items = (
                value
                if isinstance(value, list)
                else ([] if "v" not in record else [value])
            )
            idx = record.get("i")
            if (
                isinstance(idx, int)
                and not isinstance(idx, bool)
                and 0 <= idx <= len(arr)
            ):
                arr[idx:] = items
            else:
                arr.extend(items)
        elif kind == 3:
            if not keys:
                continue
            parent = _container(state, keys[:-1])
            if isinstance(parent, list) and isinstance(last, int) and (
                not isinstance(last, bool) and 0 <= last < len(parent)
            ):
                parent.pop(last)
            elif isinstance(parent, dict):
                parent.pop(last, None)
        # unknown kinds — silently skipped
    return state, bad_lines


def _chat_files(
    user_dirs: Iterable[Path],
) -> list[tuple[Path, str, Path | None]]:
    """``(file, session_id, workspace_dir|None)`` per chat store; the
    ``.jsonl`` mutation log wins over a same-id ``.json`` snapshot."""
    out: list[tuple[Path, str, Path | None]] = []

    def collect(chat_dir: Path, ws_dir: Path | None) -> None:
        if not chat_dir.is_dir():
            return
        try:
            entries = sorted(chat_dir.iterdir())
        except OSError:
            return
        by_id: dict[str, Path] = {}
        for entry in entries:
            if not entry.is_file():
                continue
            if entry.name.endswith(".jsonl"):
                sid = entry.name[: -len(".jsonl")]
            elif entry.name.endswith(".json"):
                sid = entry.name[: -len(".json")]
            else:
                continue
            if sid and (sid not in by_id or entry.name.endswith(".jsonl")):
                by_id[sid] = entry
        for sid in sorted(by_id):
            out.append((by_id[sid], sid, ws_dir))

    for user_dir in user_dirs:
        for name in _GLOBAL_DIRS:
            collect(user_dir / "globalStorage" / name, None)
        ws_root = user_dir / "workspaceStorage"
        if ws_root.is_dir():
            try:
                ws_entries = sorted(ws_root.iterdir())
            except OSError:
                ws_entries = []
            for entry in ws_entries:
                if entry.is_dir():
                    collect(entry / "chatSessions", entry)
    return out


_PATH_LIKE_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/|\\\\|file:)")

# file:/// URIs on Windows parse as '/C:/repo' — the leading slash is a
# URI artifact, not part of the OS path. Strip it when a drive letter
# follows so --within comparisons and file checks see 'C:/repo'.
_WINDOWS_URI_PATH_RE = re.compile(r"^/([A-Za-z]:[\\/].*)$")


def _uri_path(uri: str) -> str | None:
    """Filesystem path from a file:// URI, or None when not a file URI."""
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "file" or not parsed.path:
        return None
    path = urllib.parse.unquote(parsed.path)
    m = _WINDOWS_URI_PATH_RE.match(path)
    return m.group(1) if m else path


def _workspace_dir(
    ws_dir: Path | None, warnings: list[str]
) -> str | None:
    """Real path for a ``workspaceStorage/<wsDir>`` entry: the sibling
    ``workspace.json`` ``folder`` URI wins; else the URL-decoded dir
    name *only when it still looks like a path* — a bare hash id (the
    name VS Code actually mints for hashed workspaces) is not a path,
    and returning it would feed a fake working_dir into ``--within``
    and file checks. Unresolvable → ``None`` + warning."""
    if ws_dir is None:
        return None
    ws_json = ws_dir / "workspace.json"
    if ws_json.is_file():
        try:
            data = json.loads(
                ws_json.read_text(encoding="utf-8", errors="replace")
            )
        except (json.JSONDecodeError, OSError):
            data = None
        folder = data.get("folder") if isinstance(data, dict) else None
        if isinstance(folder, str):
            resolved = _uri_path(folder)
            if resolved is not None:
                return resolved
            if not urllib.parse.urlparse(folder).scheme:
                return folder
    name = urllib.parse.unquote(ws_dir.name)
    if name.startswith("file:"):
        resolved = _uri_path(name)
        if resolved:
            return resolved
    elif _PATH_LIKE_RE.search(name):
        return name
    warnings.append(
        f"workspaceStorage/{ws_dir.name}: no resolvable folder path "
        "(hashed workspace id) — working_dir left unset"
    )
    return None


def _strings(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)
    elif isinstance(node, str):
        yield node


def _is_tool_part(part: dict[str, Any]) -> bool:
    kind = str(part.get("kind") or "").lower()
    if any(m in kind for m in _TOOL_PART_MARKERS):
        return True
    return any(
        key in part for key in ("toolCallId", "toolId", "toolName",
                                "invocationMessage", "toolSpecificData")
    )


def _sniff_command(node: Any) -> str | None:
    """First terminal-looking command string in a tool-invocation part."""
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k) in _COMMAND_KEYS and isinstance(v, str) and v.strip():
                return v
            found = _sniff_command(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _sniff_command(v)
            if found:
                return found
    return None


def _sniff_exit(node: Any) -> int | None:
    """First recorded exit code in a tool-invocation part (terminal
    state objects like ``terminalCommandState.exitCode``); ``None``
    when the part records no completion state at all."""
    if isinstance(node, dict):
        for k, v in node.items():
            if (
                str(k).lower() in ("exitcode", "exit_code")
                and isinstance(v, int)
                and not isinstance(v, bool)
            ):
                return v
            found = _sniff_exit(v)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _sniff_exit(v)
            if found is not None:
                return found
    return None


def _tool_call(part: dict[str, Any], fallback: str, ts: float | None) -> EvidenceToolCall:
    """One tool-invocation response part as a tool call — the part dict
    is the payload; a sniffed command is added as ``command`` so the
    verifier's digging finds it. A command-carrying invocation is a
    terminal run: its opaque part kind (``"toolInvocation"``) must not
    mask execute-ness, so the payload kind normalizes to ``"execute"``
    and the original moves to ``part_kind`` (also kept on
    ``EvidenceToolCall.kind``)."""
    cid = part.get("toolCallId") or part.get("callId") or part.get("id")
    kind = (
        part.get("toolName") or part.get("toolId") or part.get("kind")
        or ""
    )
    payload = dict(part)
    command = _sniff_command(part)
    if command:
        payload.setdefault("command", command)
        orig = payload.get("kind")
        if orig is not None and orig != "execute":
            payload.setdefault("part_kind", orig)
        payload["kind"] = "execute"
    exit_code = _sniff_exit(part)
    if exit_code is not None:
        payload.setdefault("exit_code", exit_code)
    return EvidenceToolCall(
        tool_call_id=str(cid) if cid is not None else fallback,
        kind=str(kind),
        payload=payload,
        update_payload=None,
        ts_ms=ts,
    )


def _session_evidence(
    doc: dict[str, Any],
    session_id: str,
    ref: str,
    working_dir: str | None,
    warnings: list[str],
) -> SessionEvidence:
    messages: list[EvidenceMessage] = []
    tool_calls: list[EvidenceToolCall] = []
    created = epoch_ms(doc.get("creationDate"))
    requests = doc.get("requests")
    if isinstance(requests, list):
        for i, request in enumerate(requests):
            if not isinstance(request, dict):
                continue
            ts = epoch_ms(request.get("timestamp")) or created
            user_text = _field_text(request.get("message"))
            if user_text.strip():
                messages.append(
                    EvidenceMessage(role="user", text=user_text, ts_ms=ts)
                )
            resp_ts = epoch_ms(request.get("responseTimestamp")) or ts
            response = request.get("response")
            if isinstance(response, list):
                parts = response
            elif isinstance(response, dict) and isinstance(
                response.get("value"), list
            ):
                parts = response["value"]
            else:
                parts = []
            text_parts: list[str] = []
            n_tools = 0
            for part in parts:
                if not isinstance(part, dict):
                    if isinstance(part, str):
                        text_parts.append(part)
                    continue
                if _is_tool_part(part):
                    n_tools += 1
                    tool_calls.append(
                        _tool_call(part, f"{session_id}:{i}:{n_tools}", resp_ts)
                    )
                    continue
                text = _field_text(part)
                if text.strip():
                    text_parts.append(text)
            if not parts:
                text = _field_text(response)
                if text.strip():
                    text_parts.append(text)
            if text_parts:
                messages.append(
                    EvidenceMessage(
                        role="assistant",
                        text="\n".join(text_parts),
                        ts_ms=resp_ts,
                    )
                )
    return SessionEvidence(
        surface=SURFACE,
        native_id=session_id,
        ref=ref,
        started_at=iso_from_ms(created),
        working_dir=working_dir,
        messages=messages,
        tool_calls=tool_calls,
        warnings=warnings,
    )


class VscodeChatProvider:
    """VS Code ``User`` dirs (env-detected or explicit)."""

    surface = SURFACE

    def __init__(self, user_dirs: Iterable[str | Path] | None = None) -> None:
        self._dirs = (
            [Path(d).expanduser() for d in user_dirs]
            if user_dirs is not None
            else None
        )

    def _resolve(self, env: Mapping[str, str] | None) -> list[Path]:
        if self._dirs is not None:
            return self._dirs
        return [
            d for d in vscode_user_dirs(env_or_default(env)) if d.is_dir()
        ]

    def available(self, env: Mapping[str, str] | None = None) -> bool:
        return bool(self._resolve(env))

    def sessions(
        self, env: Mapping[str, str] | None = None
    ) -> list[SessionEvidence]:
        out: list[SessionEvidence] = []
        for path, session_id, ws_dir in _chat_files(self._resolve(env)):
            warnings: list[str] = []
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            doc, bad = _rebuild(text)
            if bad:
                warnings.append(f"{bad} malformed mutation line(s) skipped")
            if not (_KNOWN_KEYS & set(doc)):
                continue  # present but not a chat session document
            out.append(
                _session_evidence(
                    doc, session_id, str(path),
                    _workspace_dir(ws_dir, warnings), warnings,
                )
            )
        out.sort(key=lambda s: (s.started_at or "", s.native_id),
                 reverse=True)
        return out


PROVIDER_CLASS = VscodeChatProvider
