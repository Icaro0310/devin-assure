"""The normalized session view every adapter produces."""

from __future__ import annotations

from dataclasses import dataclass

from devin_qa_pack.claims import Claim
from devin_qa_pack.verify import ParsedToolCall


@dataclass(frozen=True)
class SourceSession:
    """One session's claims plus its recorded tool calls.

    ``raw_call_count`` is the number of evidence rows *before* decoding —
    when rows exist but none parse, ground truth is unreadable and claims
    resolve ``unverifiable`` rather than ``disputed``.
    """

    source: str  # adapter name: "devin" | "aider" | "claude-code" | ...
    session_id: str
    title: str | None
    working_directory: str
    claims: tuple[Claim, ...]
    calls: tuple[ParsedToolCall, ...]
    raw_call_count: int
