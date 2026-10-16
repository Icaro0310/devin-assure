"""Extract deliverable claims from a session's evidence messages.

A *claim* is an assertion the agent made about delivered work: tests
passed, a commit exists, a file was created/changed, work was pushed.
Only agent-role messages are scanned — a user prompt quoting the same
words is not a delivery claim.

The input is the canonical :class:`EvidenceMessage` — store-specific
payload decoding (``chat_message`` JSON for the Devin store) happens in
the evidence providers, so this module only ever sees plain role+text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from devin_qa_pack.evidence import EvidenceMessage, normalize_role

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
    node_id: int | str
    excerpt: str


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
        r"\b(?:deployed|live|hosted|available|published|running)\b", line, re.IGNORECASE
    ):
        for m in _URL_RE.finditer(line):
            yield URL, m.group(0).rstrip(".,);'\"")


def _collect(
    session_id: str,
    node_id: int | str,
    text: str,
    claims: list[Claim],
    seen: set[tuple[str, str]],
) -> None:
    for line in text.splitlines():
        for kind, detail in _claims_in_line(line):
            if not detail or (kind, detail) in seen:
                continue
            seen.add((kind, detail))
            claims.append(
                Claim(
                    kind=kind,
                    detail=detail,
                    session_id=session_id,
                    node_id=node_id,
                    excerpt=_excerpt(line),
                )
            )


def extract_claims(
    messages: Iterable[EvidenceMessage], session_id: str = ""
) -> list[Claim]:
    """All claims across ``messages``, deduplicated by ``(kind, detail)``.

    ``Claim.node_id`` cites the surface's real node id
    (``EvidenceMessage.source_id``, e.g. the devin-cli
    ``message_nodes.node_id``) when the provider supplies one; otherwise
    the message's positional index — same convention as
    :func:`claims_from_pairs`.
    """
    claims: list[Claim] = []
    seen: set[tuple[str, str]] = set()
    for position, message in enumerate(messages, 1):
        if message.role in _SKIP_ROLES:
            continue
        node_id = (
            message.source_id
            if message.source_id is not None
            else position
        )
        _collect(session_id, node_id, message.text, claims, seen)
    return claims


def claims_from_pairs(
    pairs: Iterable[tuple[str | None, str]],
    session_id: str,
) -> list[Claim]:
    """Claims over pre-decoded ``(role, text)`` pairs — the adapter input."""
    return extract_claims(
        (
            EvidenceMessage(
                role=normalize_role(role), text=text, ts_ms=None
            )
            for role, text in pairs
        ),
        session_id,
    )
