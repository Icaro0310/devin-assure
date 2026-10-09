"""Devin MCP adapter — audits cloud sessions via the official Devin MCP.

The published REST API exposes session messages as flat narrative text —
not usable as tool-call evidence. The real evidence surface lives behind
the hosted MCP server (``https://mcp.devin.ai/mcp``): the
``devin_session_events`` tool returns a typed event stream whose ``shell``
category is the cloud analog of ``tool_call_state`` — verified live against
a Pro-plan PAT on 2026-10-06 (see devin-internals-spec
docs/EVIDENCE-EQUIVALENCE.md).

Event-to-call mapping:

- ``shell_process_started`` opens a call keyed by ``process_id``;
  ``contents.command`` (and ``chain[].command``) become ``commands``.
- ``terminal_update`` appends base64-decoded stdout chunks to the call's
  evidence text.
- ``shell_process_completed`` resolves status from ``exit_code`` and adds
  ``output_trunc`` to the evidence text.
- ``file``/``git``/``mcp``/``search``/``browser`` events become generic
  calls — ``kind`` from the category, ``status`` from the event-type
  suffix, evidence text from every string in ``contents``.
- ``devin_message`` events feed claim extraction (the agent's narrative);
  ``initial_user_message`` feeds the intent side (skipped by the claim
  extractor, like every user turn).

``raw_call_count`` counts every tool-category event seen in the listing —
if a ``details`` batch fails, those events stay unreadable ground truth and
claims resolve ``unverifiable`` rather than ``disputed`` (same contract as
NULL payloads in the local store).

Auth: ``DEVIN_API_KEY`` env var holding a ``cog_``-prefixed token (service
user key or PAT). The key never leaves the ``Authorization`` header.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from devin_qa_pack.adapters.base import SourceSession
from devin_qa_pack.claims import claims_from_pairs
from devin_qa_pack.verify import ParsedToolCall, _http_statuses, _strings

SOURCE = "mcp"

DEFAULT_MCP_URL = "https://mcp.devin.ai/mcp"
_LIST_PAGE = 100          # the MCP tool caps `first` at 100
_DETAILS_BATCH = 20       # event_ids cap per details call
_MAX_EVENTS = 2000        # safety bound on pagination
_DETAILS_CAP = 400        # bound details fetches on huge sessions

_TOOL_CATEGORIES = {"shell", "file", "search", "browser", "mcp", "git"}
_WRITE_CATEGORIES = {"file"}

_EVENT_LINE_RE = re.compile(
    r"^\[(event-[0-9a-f]+)\]\s+\S+\s+\S+\s+UTC\s+"
    r"([<>]{3})\s+([a-z_]+)\s+\(([a-z]+)\):\s*(.*)$"
)
_CURSOR_RE = re.compile(r"Pass after=(\S+)")
_DETAIL_HEADER_RE = re.compile(
    r"^--- (event-[0-9a-f]+) ---\s*$", re.MULTILINE)

_STATUS_SUFFIXES = (
    ("completed", "completed"), ("finished", "completed"),
    ("succeeded", "completed"), ("failed", "failed"),
    ("error", "failed"), ("started", "pending"),
    ("running", "pending"),
)


class McpError(RuntimeError):
    """Raised when the Devin MCP call fails or returns an error."""


def _tool_text(response: dict[str, Any]) -> str:
    """Extract the text payload of a ``tools/call`` result."""
    result = response.get("result") or {}
    if result.get("isError"):
        content = result.get("content") or []
        msg = content[0].get("text", "unknown MCP error") if content else "unknown MCP error"
        raise McpError(msg)
    content = result.get("content") or []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text") or "")
    return ""


def call_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    api_key: str,
    base_url: str = DEFAULT_MCP_URL,
    timeout: int = 30,
    _rid: list[int] | None = None,
) -> str:
    """One JSON-RPC ``tools/call`` against the hosted Devin MCP.

    The server is stateless (no ``Mcp-Session-Id`` handshake needed) and
    answers either plain JSON or an SSE frame — the ``data:`` payload is
    what we parse.
    """
    rid = [0] if _rid is None else _rid
    rid[0] += 1
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": rid[0],
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {api_key}",
    }
    # Some tools require explicit org context for PAT/account-scoped
    # tokens (server asks for X-Org-Id when it cannot resolve one).
    org_id = os.environ.get("DEVIN_ORG_ID")
    if org_id:
        headers["X-Org-Id"] = org_id
    req = urllib.request.Request(base_url, data=body, headers=headers)
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode(
            "utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise McpError(f"MCP HTTP {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise McpError(f"MCP unreachable: {e.reason}") from e
    text = raw
    for line in raw.splitlines():
        if line.startswith("data:"):
            text = line[len("data:"):].strip()
            break
    try:
        response = json.loads(text)
    except json.JSONDecodeError as e:
        raise McpError(f"unparseable MCP response: {text[:200]}") from e
    if isinstance(response, dict) and "error" in response:
        raise McpError(str(response["error"]))
    return _tool_text(response)


def _parse_list(text: str) -> tuple[list[dict[str, str]], str | None]:
    """Parse the ``action=list`` text block.

    Lines look like::

        [event-abc123] 2026-10-06 14:44:45 UTC >>(arrow) shell_process_started (shell): exec: echo hi
        More results available. Pass after=CURSOR to fetch the next page.
    """
    events: list[dict[str, str]] = []
    cursor: str | None = None
    for line in text.splitlines():
        m = _EVENT_LINE_RE.match(line.strip())
        if m:
            events.append({
                "event_id": m.group(1),
                "direction": "incoming" if m.group(2).startswith("<") else "outgoing",
                "event_type": m.group(3),
                "category": m.group(4),
                "summary": m.group(5),
            })
            continue
        c = _CURSOR_RE.search(line)
        if c:
            cursor = c.group(1)
    return events, cursor


def list_events(
    session_id: str,
    *,
    api_key: str,
    base_url: str = DEFAULT_MCP_URL,
    max_events: int = _MAX_EVENTS,
    _rid: list[int] | None = None,
    _client=call_tool,
) -> list[dict[str, str]]:
    """All event summaries for a session, walking ``after`` cursors."""
    out: list[dict[str, str]] = []
    after: str | None = None
    while len(out) < max_events:
        args: dict[str, Any] = {
            "action": "list",
            "session_id": session_id,
            "first": _LIST_PAGE,
        }
        if after:
            args["after"] = after
        text = _client(
            "devin_session_events", args,
            api_key=api_key, base_url=base_url, _rid=_rid)
        page, cursor = _parse_list(text)
        out.extend(page)
        if not cursor or not page:
            break
        after = cursor
    return out


_TYPE_LINE_RE = re.compile(r"type:\s+([a-z_]+)\s+\(([a-z]+)\)")


def _parse_details(text: str) -> dict[str, dict[str, Any]]:
    """Parse the ``action=details`` text block into event_id → fields.

    Blocks look like::

        --- event-abc123 ---
          type: shell_process_started (shell)
          direction: outgoing
          created_at: 2026-10-06 14:44:45 UTC
          contents: {
          "command": "echo hi",
          ...
        }

    ``contents`` is a pretty-printed JSON object running to the end of the
    block — it is decoded with ``JSONDecoder.raw_decode`` on everything
    after the ``contents:`` marker.
    """
    details: dict[str, dict[str, Any]] = {}
    decoder = json.JSONDecoder()
    for block in _DETAIL_HEADER_RE.split(text):
        pass  # see loop below — split() interleaves ids and bodies
    chunks = _DETAIL_HEADER_RE.split(text)
    # chunks = [prelude, id1, body1, id2, body2, ...]
    for i in range(1, len(chunks) - 1, 2):
        event_id, body = chunks[i], chunks[i + 1]
        entry: dict[str, Any] = {}
        m = _TYPE_LINE_RE.search(body)
        if m:
            entry["event_type"] = m.group(1)
            entry["category"] = m.group(2)
        idx = body.find("contents:")
        if idx != -1:
            try:
                contents, _ = decoder.raw_decode(
                    body[idx + len("contents:"):].lstrip())
                if isinstance(contents, dict):
                    entry["contents"] = contents
            except json.JSONDecodeError:
                pass
        details[event_id] = entry
    return details


def _b64_text(value: Any) -> str:
    """Decode a base64 terminal chunk; empty on any decode failure."""
    if not isinstance(value, str) or not value:
        return ""
    try:
        return base64.b64decode(value).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""


def _status_for(event_type: str, contents: dict[str, Any]) -> str:
    """Completion status from the event-type suffix, refined by exit_code."""
    if event_type == "shell_process_completed":
        code = contents.get("exit_code")
        try:
            return "completed" if int(str(code)) == 0 else "failed"
        except (TypeError, ValueError):
            return "failed" if code is not None else "unknown"
    for suffix, status in _STATUS_SUFFIXES:
        if event_type.endswith(suffix):
            return status
    return "unknown"


def _commands(contents: dict[str, Any]) -> tuple[str, ...]:
    """Command strings: top-level ``command`` plus parsed ``chain``."""
    found: list[str] = []
    cmd = contents.get("command")
    if isinstance(cmd, str) and cmd.strip():
        found.append(cmd)
    chain = contents.get("chain")
    if isinstance(chain, list):
        for link in chain:
            if isinstance(link, dict):
                c = link.get("command")
                if isinstance(c, str) and c.strip():
                    found.append(c)
    return tuple(dict.fromkeys(found))


def _kind_for(category: str, contents: dict[str, Any]) -> str:
    if category in _WRITE_CATEGORIES:
        return "write"
    if _commands(contents) or category in {"shell", "git"}:
        return "execute"
    return "other"


def load(
    session_id: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    working_directory: str = "",
    max_events: int = _MAX_EVENTS,
    details_cap: int = _DETAILS_CAP,
    _client=call_tool,  # injectable for tests
) -> SourceSession:
    """Fetch a cloud session's evidence into the normalized audit view.

    ``api_key`` defaults to ``$DEVIN_API_KEY``; ``base_url`` to the hosted
    Devin MCP. ``working_directory`` stays empty by default — cloud events
    reference the remote VM's paths, so disk/git fallbacks have no anchor.
    """
    key = api_key or os.environ.get("DEVIN_API_KEY", "")
    if not key:
        raise McpError(
            "DEVIN_API_KEY not set — expected a cog_-prefixed token")
    url = base_url or os.environ.get("DEVIN_MCP_URL", DEFAULT_MCP_URL)

    rid = [0]

    def tool(name: str, args: dict[str, Any]) -> str:
        return _client(name, args, api_key=key, base_url=url, _rid=rid)

    summaries = list_events(
        session_id, api_key=key, base_url=url, max_events=max_events,
        _rid=rid, _client=_client)

    title: str | None = None
    try:
        meta = tool("devin_session_interact",
                    {"action": "get", "session_id": session_id})
        m = re.search(r"^\s*title:\s*(.+)$", meta, re.MULTILINE)
        if m:
            title = m.group(1).strip()
    except McpError:
        pass

    tool_events = [e for e in summaries if e["category"] in _TOOL_CATEGORIES]
    message_events = [e for e in summaries
                      if e["category"] == "message"]

    want_ids = [e["event_id"] for e in tool_events + message_events]
    # A failed batch is skipped — those events stay unreadable ground
    # truth (raw_call_count still counts them).
    details: dict[str, dict[str, Any]] = {}
    ids = want_ids[:details_cap]
    for i in range(0, len(ids), _DETAILS_BATCH):
        chunk = ids[i:i + _DETAILS_BATCH]
        try:
            text = tool("devin_session_events", {
                "action": "details",
                "session_id": session_id,
                "event_ids": chunk})
        except McpError:
            continue
        details.update(_parse_details(text))

    pairs: list[tuple[str, str]] = []
    for e in message_events:
        d = details.get(e["event_id"]) or {}
        contents = d.get("contents") if isinstance(d.get("contents"), dict) else {}
        msg = contents.get("message")
        if not isinstance(msg, str):
            continue
        role = "user" if e["event_type"] in (
            "initial_user_message", "user_message") else "assistant"
        pairs.append((role, msg))

    # Group shell-family events by process_id; everything else is a
    # standalone call keyed by event_id.
    by_process: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    standalone: list[dict[str, Any]] = []
    for e in tool_events:
        d = details.get(e["event_id"]) or {}
        contents = d.get("contents") if isinstance(d.get("contents"), dict) else {}
        pid = contents.get("process_id") if contents else None
        if e["category"] == "shell" and isinstance(pid, str):
            if pid not in by_process:
                by_process[pid] = {"events": [], "contents": []}
                order.append(pid)
            by_process[pid]["events"].append(e)
            by_process[pid]["contents"].append(
                (e["event_type"], contents))
        else:
            standalone.append({"event": e, "contents": contents})

    calls: list[ParsedToolCall] = []
    for pid in order:
        items = by_process[pid]["contents"]
        started = next((c for t, c in items
                        if t == "shell_process_started"), {})
        completed = next((c for t, c in items
                          if t == "shell_process_completed"), None)
        outputs = [
            _b64_text(c.get("contents"))
            for t, c in items if t == "terminal_update"
        ]
        texts: list[str] = list(_strings(started)) + outputs
        statuses: set[int] = set(_http_statuses(started))
        status = "unknown"
        if completed is not None:
            texts.extend(_strings(completed))
            statuses |= set(_http_statuses(completed))
            status = _status_for("shell_process_completed", completed)
        elif started:
            status = "pending"
        merged: dict[str, Any] = dict(started)
        if completed:
            merged.update(completed)
        calls.append(ParsedToolCall(
            tool_call_id=pid,
            kind="execute" if started or completed else "other",
            status=status,
            commands=_commands(merged),
            search_text=" ".join(t for t in texts if t).lower().replace(
                "\\", "/"),
            http_statuses=tuple(sorted(statuses)),
        ))

    for item in standalone:
        e, contents = item["event"], item["contents"]
        texts = list(_strings(contents)) + [e["summary"]]
        calls.append(ParsedToolCall(
            tool_call_id=e["event_id"],
            kind=_kind_for(e["category"], contents),
            status=_status_for(e["event_type"], contents),
            commands=_commands(contents),
            search_text=" ".join(texts).lower().replace("\\", "/"),
            http_statuses=tuple(sorted(set(_http_statuses(contents)))),
        ))

    return SourceSession(
        source=SOURCE,
        session_id=session_id,
        title=title,
        working_directory=working_directory,
        claims=tuple(claims_from_pairs(pairs, session_id)),
        calls=tuple(calls),
        raw_call_count=len(tool_events),
    )
