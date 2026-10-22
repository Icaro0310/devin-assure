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

The input is the canonical :class:`EvidenceToolCall` — already-decoded
payload dicts from whatever store the evidence provider read. Decoding
the raw payloads is the provider's job; a call whose payloads are empty
is dropped and counted as unreadable ground truth.
"""

from __future__ import annotations

import re
import subprocess
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from devin_qa_pack.claims import (
    COMMIT,
    COVERAGE,
    DELEGATION,
    EVAL,
    FILE,
    HTTP,
    PUSH,
    TESTS,
    TESTS_PASSED,
    Claim,
    _norm_number,
)
from devin_qa_pack.evidence import EvidenceToolCall

VERIFIED = "verified"
DISPUTED = "disputed"
UNVERIFIABLE = "unverifiable"

_EXECUTE_KINDS = {"execute", "run", "terminal", "shell", "bash", "exec", "command"}
_WRITE_KINDS = {"edit", "write", "create", "delete", "move", "rename"}

# Spawn/subagent tool names — the ground truth behind ``delegation``
# claims. Devin records a ``run_subagent`` call in ``tool_call_state``;
# other surfaces carry the tool name under different keys (Copilot
# ``toolName``, Codex ``name``, VS Code ``toolId``). A name carrying
# "subagent"/"sub_agent" is a spawn whatever the prefix
# (``spawn_subagent``, ``runSubagent`` …); short aliases like ``task``
# (Copilot/Claude-style subagent tool) are matched exactly.
_SPAWN_NAME_KEYS = (
    "kind", "type", "tool", "name", "toolName", "tool_name", "toolId",
)
_SPAWN_AUX_KEYS = ("name", "toolName", "tool_name", "toolId", "tool_id")
_SPAWN_KINDS = {
    "agent", "background_agent", "create_agent", "delegate", "launch_agent",
    "parallel_agent", "run_worker", "spawn_agent", "spawn_worker",
    "sub_agent", "subagent", "task", "worker_agent",
}

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

# Pass-count evidence inside tool-call payloads: keyed result fields
# like ``{"passed": 42}`` plus count strings in captured output
# ("47 passed in 1.21s", "108 passed, 1 skipped", "1,234 tests passed").
_PASSED_KEY_NAMES = {
    "passed", "num_passed", "passed_count", "tests_passed", "n_passed",
}
# Invocation-side fields — a ``command``/``rawInput``/``title`` echoing
# "47 passed" is the request, never a recorded result.
_INPUT_KEY_NAMES = (
    {k.lower() for k in _CONTAINER_KEYS + _COMMAND_KEYS}
    | {"params", "title"}
)
_COUNT_GROUP_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
_PASSED_COUNT_RE = re.compile(
    r"(?<![\w,])(\d{1,3}(?:,\d{3})+|\d+)\s+(?:tests?\s+)?pass(?:ed|es)?\b",
    re.IGNORECASE,
)

# Coverage evidence inside tool-call payloads: keyed result fields like
# ``{"coverage": 87}``/``{"line_rate": 0.85}`` (cobertura-XML style —
# 0-1 rates scale to percent) plus percent strings in captured output
# ("coverage: 87.5%", "85% code coverage", pytest-cov's
# "TOTAL  1234  234  88%" table row, ``line-rate="0.85"`` attributes).
_COVERAGE_KEY_NAMES = {
    "coverage", "coverage_percent", "percent_covered", "line_coverage",
    "branch_coverage", "total_coverage", "cov",
}
_COVERAGE_RATE_KEYS = {"line_rate", "line-rate", "branch_rate",
                       "branch-rate"}
_COV_LIT_RE = re.compile(r"\d+(?:\.\d+)?%?")
_COVERAGE_STR_RES = (
    re.compile(
        r"\b(?:coverage|cobertura)\b[^%\n]{0,40}?"
        r"(\d{1,3}(?:\.\d+)?)\s*%",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,3}(?:\.\d+)?)\s*%\s+(?:[\w-]+\s+){0,2}"
        r"(?:coverage|cobertura)\b",
        re.IGNORECASE,
    ),
    # coverage.py's "TOTAL  1234  234  88%" table row — its single
    # percent column is the total coverage
    re.compile(
        r"^[^\S\n]*TOTAL\b[^\n]*?\b(\d{1,3}(?:\.\d+)?)\s*%",
        re.IGNORECASE | re.MULTILINE,
    ),
)
_COVERAGE_RATE_RE = re.compile(
    r"\b(?:line|branch)[-_]?rate\b[\"'\s:=]{0,3}(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Eval-score evidence: keyed result fields (``{"score": 0.92}``,
# ``{"accuracy": "85%"}``, ``{"pass_rate": "18/20"}``) plus
# score-looking strings in captured output ("eval score: 0.92",
# "accuracy=85%", "18/20"). Literals normalize through
# ``_norm_number`` — ``85%`` reads as ``85``, never rescaled to 0.85.
_EVAL_KEY_NAMES = {
    "score", "eval_score", "evalscore", "accuracy", "pass_rate",
    "passrate", "benchmark_score", "eval_result",
}
_EVAL_LIT_RE = re.compile(r"(?:\d+(?:\.\d+)?%?|\d+\s*/\s*\d+)")
_EVAL_STR_RE = re.compile(
    r"\b(?:evals?|evaluations?|benchmarks?|scores?|accuracy|"
    r"pass[ _-]?rate)\b[^.\n]{0,15}?"
    r"(\d+(?:\.\d+)?%?|\d+\s*/\s*\d+)(?![\w/])",
    re.IGNORECASE,
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


def _passed_counts(value: Any) -> Iterable[int]:
    """Pass counts asserted in a tool call's *result* fields.

    Input-side subtrees are skipped outright: a ``rawInput.command``
    carrying "47 passed" is the invocation text — it cannot corroborate
    a claimed test result.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            low = str(k).lower()
            if low in _INPUT_KEY_NAMES:
                continue
            if low in _PASSED_KEY_NAMES:
                if isinstance(v, bool):
                    pass  # JSON true/false — not a count
                elif isinstance(v, int) and v >= 0:
                    yield v
                elif isinstance(v, str) and _COUNT_GROUP_RE.fullmatch(v):
                    yield int(v.replace(",", ""))
            yield from _passed_counts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _passed_counts(v)
    elif isinstance(value, str):
        for m in _PASSED_COUNT_RE.finditer(value):
            yield int(m.group(1).replace(",", ""))


def _rate_to_percent(value: Any) -> str | None:
    """A cobertura-XML ``line-rate``-style 0-1 float as a percent literal."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        rate = float(value)
    elif isinstance(value, str) and _COV_LIT_RE.fullmatch(value.strip()):
        rate = float(value.strip().rstrip("%"))
    else:
        return None
    if 0 <= rate <= 1:
        return _norm_number(str(round(rate * 100, 6)))
    if rate <= 100:
        return _norm_number(str(rate))
    return None


def _coverage_values(value: Any) -> Iterable[str]:
    """Coverage percents asserted in a tool call's *result* fields.

    Normalized via :func:`_norm_number` so ``"85%"``, ``85.0`` and
    ``{"coverage_percent": 85}`` all land on the same literal. Input-side
    subtrees are skipped, like pass counts: a ``rawInput`` carrying a
    ``--cov-fail-under`` threshold is the request, not a measured result.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            low = str(k).lower()
            if low in _INPUT_KEY_NAMES:
                continue
            if low in _COVERAGE_KEY_NAMES:
                if isinstance(v, bool):
                    pass  # JSON true/false — not a percent
                elif isinstance(v, (int, float)) and 0 <= v <= 100:
                    yield _norm_number(str(v))
                elif isinstance(v, str) \
                        and _COV_LIT_RE.fullmatch(v.strip()):
                    yield _norm_number(v)
            elif low in _COVERAGE_RATE_KEYS:
                pct = _rate_to_percent(v)
                if pct is not None:
                    yield pct
            yield from _coverage_values(v)
    elif isinstance(value, list):
        for v in value:
            yield from _coverage_values(v)
    elif isinstance(value, str):
        for rx in _COVERAGE_STR_RES:
            for m in rx.finditer(value):
                yield _norm_number(m.group(1))
        for m in _COVERAGE_RATE_RE.finditer(value):
            pct = _rate_to_percent(m.group(1))
            if pct is not None:
                yield pct


def _eval_scores(value: Any) -> Iterable[str]:
    """Eval-score literals asserted in a tool call's *result* fields —
    keyed score fields and score-looking strings in captured output.
    Same input-side skip as pass counts: an invocation echoing
    ``eval --target 0.9`` is the request, never a recorded result."""
    if isinstance(value, dict):
        for k, v in value.items():
            low = str(k).lower()
            if low in _INPUT_KEY_NAMES:
                continue
            if low in _EVAL_KEY_NAMES:
                if isinstance(v, bool):
                    pass  # JSON true/false — not a score
                elif isinstance(v, (int, float)):
                    yield _norm_number(str(v))
                elif isinstance(v, str) \
                        and _EVAL_LIT_RE.fullmatch(v.strip()):
                    yield _norm_number(v)
            yield from _eval_scores(v)
    elif isinstance(value, list):
        for v in value:
            yield from _eval_scores(v)
    elif isinstance(value, str):
        for m in _EVAL_STR_RE.finditer(value):
            yield _norm_number(m.group(1))


@dataclass(frozen=True)
class ParsedToolCall:
    tool_call_id: str
    kind: str  # "execute" | "write" | "spawn" | "other" | "unknown"
    status: str  # "completed" | "failed" | "pending" | "unknown"
    commands: tuple[str, ...]
    search_text: str  # all string values from both payloads, normalized
    http_statuses: tuple[int, ...]  # HTTP status codes found in the payloads
    passed_counts: tuple[int, ...]  # pass counts found in the payloads
    coverage_values: tuple[str, ...] = ()  # coverage percents in the payloads
    eval_scores: tuple[str, ...] = ()      # eval-score literals in the payloads


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


def _terminal_exit(payload: dict[str, Any]) -> int | None:
    meta = payload.get("_meta")
    if not isinstance(meta, dict):
        return None
    for key in ("terminal_exit", "cognition.ai/terminal_exit"):
        te = meta.get(key)
        if isinstance(te, dict) and isinstance(te.get("exit_code"), int):
            return te["exit_code"]
    return None


def _status_of(payloads: list[dict[str, Any]]) -> str:
    # Terminal exit codes beat the status string: exec calls record
    # status="completed" even when the command exits non-zero.
    for payload in payloads:  # update first — it carries the latest state
        code = _terminal_exit(payload)
        if code is not None:
            return "completed" if code == 0 else "failed"
    # VS Code non-terminal invocations keep their outcome in
    # ``isComplete``/``resultDetails`` (no exit code exists for a
    # spawn) — read them before the generic status strings.
    for payload in payloads:
        if payload.get("isComplete") is True:
            details = payload.get("resultDetails")
            if isinstance(details, dict) and details.get("isError") is True:
                return "failed"
            return "completed"
        if payload.get("isComplete") is False:
            return "pending"
        if payload.get("resultDetails") not in (None, "", {}, []):
            return "completed"
    for payload in payloads:
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


def _is_spawn_name(value: Any) -> bool:
    """A payload field names a subagent-spawning tool."""
    if not isinstance(value, str):
        return False
    low = value.strip().lower().replace("-", "_").replace(" ", "_")
    return (
        low in _SPAWN_KINDS
        or "subagent" in low
        or "sub_agent" in low
    )


def _kind_of(payload: dict[str, Any], commands: list[str]) -> str:
    # kind/type/tool are authoritative: an ``execute`` envelope keeps
    # its command evidence even when an auxiliary field (``toolId``,
    # ``name``) names a subagent tool — otherwise a spawned ``pytest``
    # run would stop counting for test claims. A spawn name in one of
    # the explicit fields still classifies as spawn.
    held: str | None = None
    for key in ("kind", "type", "tool"):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        low = value.lower()
        if low in _EXECUTE_KINDS:
            return "execute"
        if low in _WRITE_KINDS:
            return "write"
        if _is_spawn_name(value):
            held = "spawn"
        elif held is None:
            held = "other"
    if held == "spawn":
        return "spawn"
    if any(_is_spawn_name(payload.get(key)) for key in _SPAWN_AUX_KEYS):
        return "spawn"
    if held is not None:
        return held
    title = payload.get("title")
    if isinstance(title, str) and (
        "subagent" in title.lower() or "sub-agent" in title.lower()
    ):
        return "spawn"
    if isinstance(title, str) and any(
        k in title.lower() for k in _EXECUTE_KINDS
    ):
        return "execute"
    return "execute" if commands else "unknown"


def parse_tool_call(call: EvidenceToolCall) -> ParsedToolCall | None:
    """Decode one tool call; ``None`` when both payloads are empty
    (unreadable ground truth — an empty dict carries no evidence)."""
    payloads: list[dict[str, Any]] = []
    strings: list[str] = []
    for payload in (call.update_payload, call.payload):
        if not isinstance(payload, dict) or not payload:
            continue
        payloads.append(payload)
        strings.extend(_strings(payload))
    if not payloads:
        return None
    commands = tuple(
        dict.fromkeys(c for p in payloads for c in _dig_command(p))
    )
    call_payload = payloads[-1] if len(payloads) > 1 else payloads[0]
    tool_call_id = call.tool_call_id or ""
    search = " ".join(strings + [tool_call_id])
    statuses = tuple(sorted({s for p in payloads for s in _http_statuses(p)}))
    counts = tuple(sorted({n for p in payloads for n in _passed_counts(p)}))
    coverage = tuple(
        sorted({v for p in payloads for v in _coverage_values(p)})
    )
    scores = tuple(
        sorted({v for p in payloads for v in _eval_scores(p)})
    )
    return ParsedToolCall(
        tool_call_id=tool_call_id,
        kind=_kind_of(call_payload, list(commands)),
        status=_status_of(payloads),
        commands=commands,
        search_text=search.lower().replace("\\", "/"),
        http_statuses=statuses,
        passed_counts=counts,
        coverage_values=coverage,
        eval_scores=scores,
    )


def parse_tool_calls(
    calls: Iterable[EvidenceToolCall],
) -> list[ParsedToolCall]:
    return [p for p in (parse_tool_call(c) for c in calls) if p is not None]


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args], check=False,
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
    if claim.kind == DELEGATION:
        return call.kind == "spawn"
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
    # Unreadable rows may hold the corroborating call — a mismatch in a
    # decoded one is not conclusive while ground truth stays opaque.
    if raw_n > len(calls):
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            f"{raw_n - len(calls)} tool_call_state row(s) unreadable",
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
    return VerifiedClaim(
        claim, UNVERIFIABLE, "no tool output records an HTTP status"
    )


def _verify_tests_passed(
    claim: Claim,
    calls: list[ParsedToolCall],
    raw_n: int,
) -> VerifiedClaim:
    """Pass-count claims check *output values*, like ``http`` claims: a
    claim is ``verified`` when recorded output reports the claimed count,
    ``disputed`` when outputs record a different count, ``unverifiable``
    when no output records any count (or the rows holding it are
    unreadable)."""
    try:
        claimed = int(claim.detail)
    except ValueError:
        return VerifiedClaim(
            claim, UNVERIFIABLE, f"`{claim.detail}` is not a pass count"
        )
    for c in calls:
        if claimed in c.passed_counts:
            return VerifiedClaim(
                claim, VERIFIED,
                f"tool call {c.tool_call_id} output reports "
                f"{claimed} passed",
            )
    # Unreadable rows may hold the corroborating run — a mismatch in a
    # decoded call is not conclusive while ground truth stays opaque.
    if raw_n > len(calls):
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            f"{raw_n - len(calls)} tool_call_state row(s) unreadable",
        )
    with_counts = [c for c in calls if c.passed_counts]
    if with_counts:
        c = with_counts[0]
        seen = ", ".join(str(s) for s in c.passed_counts)
        return VerifiedClaim(
            claim, DISPUTED,
            f"tool call {c.tool_call_id} output reports {seen} passed, "
            f"not {claimed}",
        )
    return VerifiedClaim(
        claim, UNVERIFIABLE, "no tool output records a pass count"
    )


def _verify_coverage(
    claim: Claim,
    calls: list[ParsedToolCall],
    raw_n: int,
) -> VerifiedClaim:
    """Coverage claims check *output values*, like ``tests_passed``
    claims: ``verified`` when a recorded percent equals the claim,
    ``disputed`` when outputs record a different percent,
    ``unverifiable`` when no output records any percent (or the rows
    holding it are unreadable)."""
    claimed = _norm_number(claim.detail)
    if not _COV_LIT_RE.fullmatch(claimed):
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            f"`{claim.detail}` is not a coverage percent",
        )
    for c in calls:
        if claimed in c.coverage_values:
            return VerifiedClaim(
                claim, VERIFIED,
                f"tool call {c.tool_call_id} output reports "
                f"{claimed}% coverage",
            )
    # Unreadable rows may hold the corroborating run — a mismatch in a
    # decoded one is not conclusive while ground truth stays opaque.
    if raw_n > len(calls):
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            f"{raw_n - len(calls)} tool_call_state row(s) unreadable",
        )
    with_values = [c for c in calls if c.coverage_values]
    if with_values:
        c = with_values[0]
        seen = ", ".join(f"{v}%" for v in c.coverage_values)
        return VerifiedClaim(
            claim, DISPUTED,
            f"tool call {c.tool_call_id} output reports {seen} "
            f"coverage, not {claimed}%",
        )
    return VerifiedClaim(
        claim, UNVERIFIABLE,
        "no tool output records a coverage percent",
    )


def _verify_eval(
    claim: Claim,
    calls: list[ParsedToolCall],
    raw_n: int,
) -> VerifiedClaim:
    """Eval-score claims check *output values*: ``verified`` when a
    recorded score literal equals the claim (same scale — an ``85%``
    claim matches a recorded 85, never a recorded 0.85), ``disputed``
    when outputs record a different score, ``unverifiable`` when no
    output records any score (or the rows holding it are unreadable)."""
    claimed = _norm_number(claim.detail)
    if not _EVAL_LIT_RE.fullmatch(claimed):
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            f"`{claim.detail}` is not an eval score",
        )
    for c in calls:
        if claimed in c.eval_scores:
            return VerifiedClaim(
                claim, VERIFIED,
                f"tool call {c.tool_call_id} output reports "
                f"score {claimed}",
            )
    # Unreadable rows may hold the corroborating run — a mismatch in a
    # decoded one is not conclusive while ground truth stays opaque.
    if raw_n > len(calls):
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            f"{raw_n - len(calls)} tool_call_state row(s) unreadable",
        )
    with_scores = [c for c in calls if c.eval_scores]
    if with_scores:
        c = with_scores[0]
        seen = ", ".join(c.eval_scores)
        return VerifiedClaim(
            claim, DISPUTED,
            f"tool call {c.tool_call_id} output reports score {seen}, "
            f"not {claimed}",
        )
    return VerifiedClaim(
        claim, UNVERIFIABLE, "no tool output records an eval score"
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
    if claim.kind == TESTS_PASSED:
        return _verify_tests_passed(claim, calls, raw_n)
    if claim.kind == COVERAGE:
        return _verify_coverage(claim, calls, raw_n)
    if claim.kind == EVAL:
        return _verify_eval(claim, calls, raw_n)
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

    if claim.kind == DELEGATION:
        # A real delegation goes through a spawn/subagent tool call.
        return VerifiedClaim(
            claim, DISPUTED, "no subagent-spawn tool call recorded"
        )

    # tests / push: the action would have left an execute record.
    return VerifiedClaim(
        claim, DISPUTED, f"no execute call matching `{claim.detail}` recorded"
    )


_ONLINE_TIMEOUT_S = 5


def _url_allowed(url: str, allow: tuple[str, ...]) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == d or host.endswith("." + d) for d in allow)


def _verify_url(
    claim: Claim, online: bool, allow: tuple[str, ...]
) -> VerifiedClaim:
    """QA-2: URL/deploy claims are offline-unverifiable by default; with
    ``--online`` + an allow-listed domain a live HEAD corroborates."""
    url = claim.detail
    if not online:
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            "offline audit — pass --online and --allow-domain to check "
            "the URL live",
        )
    if not _url_allowed(url, allow):
        return VerifiedClaim(
            claim, UNVERIFIABLE,
            f"host not in --allow-domain ({urlparse(url).hostname})",
        )
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "devin-qa-pack"})
        with urllib.request.urlopen(req, timeout=_ONLINE_TIMEOUT_S) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception as e:  # noqa: BLE001 - URLError, timeout; network is untrusted
        return VerifiedClaim(claim, UNVERIFIABLE,
                             f"live check failed: {e}")
    if status < 400:
        return VerifiedClaim(
            claim, VERIFIED, f"live HEAD {url} → HTTP {status}")
    return VerifiedClaim(
        claim, DISPUTED, f"live HEAD {url} → HTTP {status}")


def verify_claims(
    claims: Iterable[Claim],
    calls: list[ParsedToolCall],
    working_directory: str | None,
    *,
    raw_call_count: int | None = None,
    online: bool = False,
    allow_domains: tuple[str, ...] = (),
) -> list[VerifiedClaim]:
    out = []
    for c in claims:
        if c.kind == "url":
            out.append(_verify_url(c, online, allow_domains))
        else:
            out.append(verify_claim(
                c, calls, working_directory, raw_call_count=raw_call_count))
    return out
