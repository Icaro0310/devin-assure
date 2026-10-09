"""Experimental Claude Code adapter — audits session ``.jsonl`` transcripts.

Claude Code writes one JSONL transcript per session (usually under
``~/.claude/projects/<slug>/``). ``assistant`` entries carry ``text``
blocks — the claim source — and ``tool_use`` blocks; the matching
``tool_result`` blocks in ``user`` entries mark calls completed/failed
and carry the output used as evidence text.

Tool mapping: ``Bash`` → execute, ``Write``/``Edit``/``MultiEdit``/
``NotebookEdit`` → write, anything else → other (its payload text still
contributes evidence). Entries carry a ``cwd`` field used as the working
directory for git/file checks when no override is given.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from devin_qa_pack.adapters.base import SourceSession
from devin_qa_pack.claims import claims_from_pairs
from devin_qa_pack.verify import ParsedToolCall, _http_statuses, _strings

SOURCE = "claude-code"

_EXECUTE_TOOLS = {"bash", "shell", "execute", "run", "terminal"}
_WRITE_TOOLS = {"write", "edit", "multiedit", "notebookedit"}


def _entries(path: Path) -> Iterable[dict[str, Any]]:
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            yield entry


def _blocks(entry: dict[str, Any]) -> list[dict[str, Any]]:
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else entry.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", "")) for b in content
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return "" if content is None else str(content)


def _tool_kind(name: str) -> str:
    if name in _EXECUTE_TOOLS:
        return "execute"
    if name in _WRITE_TOOLS:
        return "write"
    return "other"


def load(path: str | Path, working_directory: str | None = None) -> SourceSession:
    p = Path(path).expanduser()
    pairs: list[tuple[str, str]] = []
    uses: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    status_by_id: dict[str, str] = {}
    out_by_id: dict[str, str] = {}
    cwd = working_directory

    for entry in _entries(p):
        role = entry.get("type") or ""
        if cwd is None and isinstance(entry.get("cwd"), str):
            cwd = entry["cwd"]
        for b in _blocks(entry):
            btype = b.get("type")
            if btype == "text":
                pairs.append((str(role or "assistant"), str(b.get("text", ""))))
            elif btype == "tool_use":
                tid = str(b.get("id") or f"call-{len(order) + 1}")
                if tid not in uses:
                    order.append(tid)
                uses[tid] = b
            elif btype == "tool_result":
                tid = str(b.get("tool_use_id") or "")
                out_by_id[tid] = _result_text(b.get("content"))
                status_by_id[tid] = "failed" if b.get("is_error") else "completed"

    calls: list[ParsedToolCall] = []
    for tid in order:
        use = uses[tid]
        name = str(use.get("name") or "").lower()
        inp = use.get("input") if isinstance(use.get("input"), dict) else {}
        command = inp.get("command")
        commands = (command,) if isinstance(command, str) and command.strip() else ()
        output = out_by_id.get(tid, "")
        texts = [*_strings(inp), output, name]
        calls.append(
            ParsedToolCall(
                tool_call_id=tid,
                kind=_tool_kind(name),
                status=status_by_id.get(tid, "unknown"),
                commands=commands,
                search_text=" ".join(texts).lower().replace("\\", "/"),
                http_statuses=tuple(sorted(
                    {*_http_statuses(inp), *_http_statuses(output)}
                )),
            )
        )

    session_id = f"claude-code:{p.name}"
    return SourceSession(
        source=SOURCE,
        session_id=session_id,
        title=p.name,
        working_directory=cwd or str(p.parent),
        claims=tuple(claims_from_pairs(pairs, session_id)),
        calls=tuple(calls),
        raw_call_count=len(calls),
    )
