"""The normalized session view every adapter produces."""

from __future__ import annotations

from dataclasses import dataclass

from devin_qa_pack.claims import Claim, extract_claims
from devin_qa_pack.evidence import SessionEvidence
from devin_qa_pack.verify import ParsedToolCall, parse_tool_calls


@dataclass(frozen=True)
class SourceSession:
    """One session's claims plus its recorded tool calls.

    ``raw_call_count`` is the number of evidence rows *before* decoding —
    when rows exist but none parse, ground truth is unreadable and claims
    resolve ``unverifiable`` rather than ``disputed``.
    """

    source: str  # adapter name: "devin-cli" | "aider" | "claude-code" | ...
    session_id: str
    title: str | None
    working_directory: str
    claims: tuple[Claim, ...]
    calls: tuple[ParsedToolCall, ...]
    raw_call_count: int


def source_view(
    evidence: SessionEvidence,
    session_id: str | None = None,
    title: str | None = None,
) -> SourceSession:
    """Bridge :class:`SessionEvidence` into the audit-side view: claims
    extracted from ``messages``, payloads parsed from ``tool_calls``."""
    sid = session_id or evidence.native_id
    return SourceSession(
        source=evidence.surface,
        session_id=sid,
        title=title,
        working_directory=evidence.working_dir or "",
        claims=tuple(extract_claims(evidence.messages, sid)),
        calls=tuple(parse_tool_calls(evidence.tool_calls)),
        raw_call_count=len(evidence.tool_calls),
    )
