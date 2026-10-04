"""Extract deliverable claims from a session's ``message_nodes``.

A *claim* is an assertion the agent made about delivered work: tests
passed, a commit exists, a file was created/changed, work was pushed.
Only agent-role messages are scanned — a user prompt quoting the same
words is not a delivery claim.

``chat_message`` payloads are marked *unstable* in devin-internals-spec
SCHEMA.md, so decoding is defensive: JSON dicts are checked for the
usual role/text/content keys (including content-block lists); anything
that is not JSON at all is scanned as plain text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from devin_internals.parsers.sessions import MessageNode

TESTS = "tests"
COMMIT = "commit"
FILE = "file"
PUSH = "push"
HTTP = "http"
URL = "url"

CLAIM_KINDS = (TESTS, COMMIT, FILE, PUSH, HTTP, URL)

_RUNNER_TOKENS = (
    "cargo test",
    "go test",
    "npm test",
    "pytest",
    "unittest",
    "vitest",
    "jest",
    "mocha",
    "tox",
    "nose",
)

_TESTISH = r"(?:tests?|test suite|pytest|unittest|jest|vitest|mocha|tox|nose)"
_TESTS_RE = re.compile(
    rf"\b{_TESTISH}\b[^.\n]*?\b(?:pass(?:ed|es|ing)?|green|succeed(?:ed|ing)?|ok)\b"
    rf"|\b\d+\s+(?:{_TESTISH}\s+)?passed\b",
    re.IGNORECASE,
)
_COMMIT_WORD_RE = re.compile(r"\bcommit(?:s|ted|ting)?\b", re.IGNORECASE)
_HEX_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_PUSH_RE = re.compile(
    r"\bgit\s+push\b"
    r"|\bpush(?:ed|ing)?\b[^.\n]*\b(?:origin|upstream|remote|main|master|branch)\b",
    re.IGNORECASE,
)
_FILE_VERB_RE = re.compile(
    r"\b(?:created?|wrote|written|writing|added|adding|updated?|updating|"
    r"modified|changed|edited|generated|saved|deleted|removed|renamed|moved)\b",
    re.IGNORECASE,
)
_PATH_TOKEN_RE = re.compile(
    r"(?<![\w/.-])(?:[A-Za-z]:[\\/])?[A-Za-z0-9_-]+(?:[\\/][\w.@-]+)*"
    r"\.[A-Za-z0-9]{2,10}\b"
)

# "the API returned 200" / "endpoint responded with 404" / "HTTP 500" /
# "status code: 200" — the captured group is the claimed status code.
_URL_RE = re.compile(r"https://[\w.-]+(?::\d+)?(?:/[\w./?%&=~#+-]*)?")

_HTTP_CLAIM_RES = (
    re.compile(
        r"\b(?:returned|responded|replied|answered|came\s+back)\b"
        r"[^.\n]*?\b([1-5]\d{2})\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bHTTP\s+(?:status\s*(?:code)?\s*)?([1-5]\d{2})\b",
               re.IGNORECASE),
    re.compile(
        r"\bstatus\s*(?:code)?\s*(?:of|was|is|:|=)\s*([1-5]\d{2})\b",
        re.IGNORECASE,
    ),
)

_SKIP_ROLES = {"user", "human"}
_MAX_EXCERPT = 160


@dataclass(frozen=True)
class Claim:
    """One deliverable claim found in an agent message.

    ``detail`` carries the checkable part: the test-runner name (or
    ``"tests"`` when generic), the commit hash, the claimed file path, or
    ``"push"``. ``excerpt`` is the source line, truncated — evidence, not
    full row content.
    """

    kind: str
    detail: str
    session_id: str
    node_id: int
    excerpt: str


def _decode_message(chat_message: str) -> tuple[str | None, str]:
    """Return ``(role, text)``. Non-JSON payloads become ``(None, raw)``."""
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


def _excerpt(line: str) -> str:
    line = " ".join(line.split())
    return line if len(line) <= _MAX_EXCERPT else line[: _MAX_EXCERPT - 1] + "…"


def _normalize_path(token: str) -> str:
    return token.replace("\\", "/").rstrip(".,;:!?)]}>")


def _runner_in(line: str) -> str | None:
    low = line.lower()
    for token in _RUNNER_TOKENS:  # multi-word runners first
        if re.search(rf"\b{re.escape(token)}\b", low):
            return token
    return None


def _claims_in_line(line: str) -> Iterable[tuple[str, str]]:
    if _TESTS_RE.search(line):
        yield TESTS, _runner_in(line) or TESTS
    if _COMMIT_WORD_RE.search(line):
        for m in _HEX_RE.finditer(line):
            yield COMMIT, m.group(0).lower()
    if _PUSH_RE.search(line):
        yield PUSH, PUSH
    if _FILE_VERB_RE.search(line):
        for m in _PATH_TOKEN_RE.finditer(line):
            yield FILE, _normalize_path(m.group(0))
    for rx in _HTTP_CLAIM_RES:
        for m in rx.finditer(line):
            yield HTTP, m.group(1)
    if re.search(
        r"\b(?:deployed|live|hosted|available|published|running)\b", line, re.I
    ):
        for m in _URL_RE.finditer(line):
            yield URL, m.group(0).rstrip(".,);'\"")


def extract_claims(nodes: Iterable[MessageNode]) -> list[Claim]:
    """All claims across ``nodes``, deduplicated by ``(kind, detail)``."""
    claims: list[Claim] = []
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        role, text = _decode_message(node.chat_message)
        if role in _SKIP_ROLES:
            continue
        for line in text.splitlines():
            for kind, detail in _claims_in_line(line):
                if not detail or (kind, detail) in seen:
                    continue
                seen.add((kind, detail))
                claims.append(
                    Claim(
                        kind=kind,
                        detail=detail,
                        session_id=node.session_id,
                        node_id=node.node_id,
                        excerpt=_excerpt(line),
                    )
                )
    return claims
