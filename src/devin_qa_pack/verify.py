"""Cross-check claims against ``tool_call_state`` ground truth and git.

The Devin-native check: a claim like "tests passed" must be backed by a
recorded run/execute call whose command ran a test runner and finished
successfully. Claims resolve to one of three statuses:

- ``verified``    — ground truth confirms the claim.
- ``disputed``    — ground truth contradicts it (no corroborating call for
  an action claim, a matching call that failed, a repo that lacks the
  commit, a missing file on disk).
- ``unverifiable`` — the evidence needed is absent or unreadable (NULL or
  unparseable tool-call payloads, file claims with no working dir on disk).

``tool_call_json``/``tool_call_update_json`` are *unstable* per
devin-internals-spec SCHEMA.md, so parsing tries the common ACP field
names and degrades gracefully: a row whose payloads both fail to decode is
dropped and counted as unreadable ground truth.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from devin_internals.parsers.sessions import ToolCallState

from devin_qa_pack.claims import COMMIT, FILE, HTTP, PUSH, TESTS, Claim

VERIFIED = "verified"
DISPUTED = "disputed"
UNVERIFIABLE = "unverifiable"

_EXECUTE_KINDS = {"execute", "run", "terminal", "shell", "bash", "exec", "command"}
_WRITE_KINDS = {"edit", "write", "create", "delete", "move", "rename"}

_STATUS_COMPLETED = {"completed", "success", "succeeded", "done", "finished"}
_STATUS_FAILED = {"failed", "error", "errored", "cancelled", "canceled"}
_STATUS_PENDING = {"pending", "in_progress", "in-progress", "running", "queued"}

_COMMAND_KEYS = ("command", "cmd")
_CONTAINER_KEYS = ("rawInput", "raw_input", "input", "args", "arguments", "parameters")

_TEST_CMD_RE = re.compile(
    r"\b(?:pytest|unittest|jest|vitest|mocha|tox|nose|test)\b", re.IGNORECASE
)
_PUSH_CMD_RE = re.compile(r"\bgit\s+push\b|\bpush\b", re.IGNORECASE)

# HTTP-status evidence inside tool-call payloads: keyed fields like
# ``{"status_code": 200}`` plus status-looking strings in captured output
# (``HTTP/1.1 200 OK``, ``Status: 404``, ``200 OK``).
_HTTP_KEY_NAMES = {
    "status", "status_code", "statuscode", "http_status", "httpstatus",
    "response_code", "responsecode",
}
_HTTP_VERSION_RE = re.compile(r"\bHTTP/\d(?:\.\d)?\s+([1-5]\d{2})\b",
                              re.IGNORECASE)
_HTTP_LABEL_RE = re.compile(
    r"\bstatus(?:[_ ]?code)?\s*[:=]\s*([1-5]\d{2})\b", re.IGNORECASE
)
_HTTP_REASONS = (
    "OK", "Created", "Accepted", "No Content", "Moved Permanently", "Found",
    "See Other", "Not Modified", "Bad Request", "Unauthorized", "Forbidden",
    "Not Found", "Method Not Allowed", "Conflict", "Gone",
    "Internal Server Error", "Bad Gateway", "Service Unavailable",
    "Gateway Timeout",
)
_HTTP_REASON_RE = re.compile(
    r"\b([1-5]\d{2})\s+(?:"
    + "|".join(re.escape(r) for r in _HTTP_REASONS) + r")\b"
)


def _http_statuses(value: Any) -> Iterable[int]:
    """HTTP status codes asserted anywhere in a tool-call payload."""
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in _HTTP_KEY_NAMES:
                if isinstance(v, bool):
                    pass  # JSON true/false — not a status code
                elif isinstance(v, int) and 100 <= v <= 599:
                    yield v
                elif isinstance(v, str) and v.isdigit() \
                        and 100 <= int(v) <= 599:
                    yield int(v)
            yield from _http_statuses(v)
    elif isinstance(value, list):
        for v in value:
            yield from _http_statuses(v)
    elif isinstance(value, str):
        for rx in (_HTTP_VERSION_RE, _HTTP_LABEL_RE, _HTTP_REASON_RE):
            for m in rx.finditer(value):
                yield int(m.group(1))


@dataclass(frozen=True)
class ParsedToolCall:
    tool_call_id: str
    kind: str  # "execute" | "write" | "other" | "unknown"
    status: str  # "completed" | "failed" | "pending" | "unknown"
    commands: tuple[str, ...]
    search_text: str  # all string values from both payloads, normalized
    http_statuses: tuple[int, ...]  # HTTP status codes found in the payloads


@dataclass(frozen=True)
class VerifiedClaim:
    claim: Claim
    status: str
    evidence: str


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _dig_command(payload: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in _COMMAND_KEYS:
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            found.append(v)
    for key in _CONTAINER_KEYS:
        inner = payload.get(key)
        if isinstance(inner, dict):
            found.extend(_dig_command(inner))
    return found


def _status_of(payloads: list[dict[str, Any]]) -> str:
    for payload in payloads:  # update first — it carries the latest state
        for key in ("status", "state"):
            raw = payload.get(key)
            if isinstance(raw, str):
                low = raw.lower()
                if low in _STATUS_COMPLETED:
                    return "completed"
                if low in _STATUS_FAILED:
                    return "failed"
                if low in _STATUS_PENDING:
                    return "pending"
    for payload in payloads:
        for key in ("exitCode", "exit_code"):
            code = payload.get(key)
            if isinstance(code, int):
                return "completed" if code == 0 else "failed"
    return "unknown"


def _kind_of(payload: dict[str, Any], commands: list[str]) -> str:
    raw = payload.get("kind") or payload.get("type") or payload.get("tool")
    if isinstance(raw, str):
        low = raw.lower()
        if low in _EXECUTE_KINDS:
            return "execute"
        if low in _WRITE_KINDS:
            return "write"
        return "other"
    title = payload.get("title")
    if isinstance(title, str) and any(
        k in title.lower() for k in _EXECUTE_KINDS
    ):
        return "execute"
    return "execute" if commands else "unknown"


def parse_tool_call(state: ToolCallState) -> ParsedToolCall | None:
    """Decode one ``tool_call_state`` row; ``None`` when both payloads are
    absent or fail to decode (unreadable ground truth)."""
    payloads: list[dict[str, Any]] = []
    strings: list[str] = []
    for raw in (state.tool_call_update_json, state.tool_call_json):
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            payloads.append(data)
            strings.extend(_strings(data))
    if not payloads:
        return None
    commands = tuple(
        dict.fromkeys(c for p in payloads for c in _dig_command(p))
    )
    call_payload = payloads[-1] if len(payloads) > 1 else payloads[0]
    search = " ".join(strings + [state.tool_call_id])
    statuses = tuple(sorted({s for p in payloads for s in _http_statuses(p)}))
    return ParsedToolCall(
        tool_call_id=state.tool_call_id,
        kind=_kind_of(call_payload, list(commands)),
        status=_status_of(payloads),
        commands=commands,
        search_text=search.lower().replace("\\", "/"),
        http_statuses=statuses,
    )


def parse_tool_calls(states: Iterable[ToolCallState]) -> list[ParsedToolCall]:
    return [p for p in (parse_tool_call(s) for s in states) if p is not None]


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _commit_in_repo(working_directory: str, sha: str) -> bool | None:
    """``True``/``False`` when ``wd`` is a repo; ``None`` when it is not."""
    wd = Path(working_directory)
    if not working_directory or not wd.is_dir():
        return None
    inside = _git(["rev-parse", "--is-inside-work-tree"], wd)
    if inside is None or inside.returncode != 0:
        return None
    result = _git(["cat-file", "-e", f"{sha}^{{commit}}"], wd)
    return None if result is None else result.returncode == 0


def _matches(claim: Claim, call: ParsedToolCall) -> bool:
    if claim.kind == TESTS:
        if call.kind != "execute":
            return False
        if claim.detail == TESTS:
            return any(_TEST_CMD_RE.search(c) for c in call.commands)
        needle = claim.detail.lower()
        return any(needle in c.lower() for c in call.commands)
    if claim.kind == PUSH:
        return call.kind == "execute" and any(
            _PUSH_CMD_RE.search(c) for c in call.commands
        )
    if claim.kind == COMMIT:
        return claim.detail in call.search_text
    if claim.kind == FILE:
        return claim.detail.lower() in call.search_text
    return False


def _evidence_for(kind: str, call: ParsedToolCall) -> str:
    if call.commands:
        return f"`{call.commands[0]}` {call.status} ({call.tool_call_id})"
    return f"tool call {call.tool_call_id} {call.status}"


def _verify_http(
    claim: Claim,
    calls: list[ParsedToolCall],
    raw_n: int,
) -> VerifiedClaim:
    """HTTP-status claims check *output values*, not call state: a claim is
    ``verified`` when a recorded status equals it, ``disputed`` when outputs
    record a different status, ``unverifiable`` when no output records any
    status (or the rows holding it are unreadable)."""
    try:
        claimed = int(claim.detail)
    except ValueError:
        return VerifiedClaim(
            claim, UNVERIFIABLE, f"`{claim.detail}` is not a status code"
        )
    for c in calls:
        if claimed in c.http_statuses:
            return VerifiedClaim(
                claim, VERIFIED,
                f"tool call {c.tool_call_id} output reports HTTP {claimed}",
            )
    with_status = [c for c in calls if c.http_statuses]
    if with_status:
        c = with_status[0]
        seen = ", ".join(str(s) for s in c.http_statuses)
        return VerifiedClaim(
            claim, DISPUTED,
            f"tool call {c.tool_call_id} output reports HTTP {seen}, "
            f"not {claimed}",
        )
    if raw_n > len(calls):
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            f"{raw_n - len(calls)} tool_call_state row(s) unreadable",
        )
    return VerifiedClaim(
        claim, UNVERIFIABLE, "no tool output records an HTTP status"
    )


def verify_claim(
    claim: Claim,
    calls: list[ParsedToolCall],
    working_directory: str | None,
    *,
    raw_call_count: int | None = None,
) -> VerifiedClaim:
    """Resolve one claim to verified/disputed/unverifiable.

    ``raw_call_count`` is the number of ``tool_call_state`` rows before
    parsing — when rows exist but none decode, ground truth is unreadable
    and the claim is unverifiable rather than disputed.
    """
    raw_n = len(calls) if raw_call_count is None else raw_call_count
    if claim.kind == HTTP:
        return _verify_http(claim, calls, raw_n)
    matched = [c for c in calls if _matches(claim, c)]

    if any(c.status == "completed" for c in matched):
        return VerifiedClaim(
            claim, VERIFIED,
            _evidence_for(claim.kind, next(c for c in matched if c.status == "completed")),
        )
    if any(c.status == "failed" for c in matched):
        return VerifiedClaim(
            claim, DISPUTED,
            _evidence_for(claim.kind, next(c for c in matched if c.status == "failed")),
        )
    if matched:
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            f"matching call {matched[0].tool_call_id} still {matched[0].status}",
        )

    # No matching call. Unreadable rows mean we cannot say the record is
    # really empty — the corroborating call may be inside them.
    if raw_n > len(calls):
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            f"{raw_n - len(calls)} tool_call_state row(s) unreadable",
        )

    if claim.kind == FILE:
        wd = Path(working_directory) if working_directory else None
        if wd is not None and wd.is_dir():
            target = wd / claim.detail
            if target.exists():
                return VerifiedClaim(claim, VERIFIED, f"{target} exists on disk")
            return VerifiedClaim(
                claim, DISPUTED, f"{target} not found under {wd}"
            )
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            "no matching tool call and working dir not on disk",
        )

    if claim.kind == COMMIT:
        in_repo = (
            _commit_in_repo(working_directory, claim.detail)
            if working_directory
            else None
        )
        if in_repo is True:
            return VerifiedClaim(
                claim, VERIFIED, f"{claim.detail} present in git log at {working_directory}"
            )
        if in_repo is False:
            return VerifiedClaim(
                claim, DISPUTED, f"{claim.detail} absent from git log at {working_directory}"
            )
        return VerifiedClaim(
            claim, DISPUTED,
            "no commit call recorded and no repo on disk to check",
        )

    # tests / push: the action would have left an execute record.
    return VerifiedClaim(
        claim, DISPUTED, f"no execute call matching `{claim.detail}` recorded"
    )


def verify_claims(
    claims: Iterable[Claim],
    calls: list[ParsedToolCall],
    working_directory: str | None,
    *,
    raw_call_count: int | None = None,
) -> list[VerifiedClaim]:
    return [
        verify_claim(c, calls, working_directory, raw_call_count=raw_call_count)
        for c in claims
    ]
