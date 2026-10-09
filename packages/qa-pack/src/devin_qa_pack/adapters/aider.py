"""Experimental Aider adapter — audits ``.aider.chat.history.md``.

Aider records its chat history as markdown: ``####`` lines are user
turns, ``>`` lines are commands issued through aider and everything else
is assistant prose (the claim source).

Evidence is deliberately weak where the format is weak: a recorded
``/run`` or ``/test`` command becomes an execute call with ``unknown``
status — the history does not persist exit codes — so test claims
resolve ``unverifiable``, never a fake ``verified``. Commit claims are
still checkable: aider auto-commits, so the repo's own ``git log`` is
ground truth whenever the working directory is on disk.
"""

from __future__ import annotations

from pathlib import Path

from devin_qa_pack.adapters.base import SourceSession
from devin_qa_pack.claims import claims_from_pairs
from devin_qa_pack.verify import ParsedToolCall

SOURCE = "aider"

_RUN_SLASH = ("/run", "/test")


def _shell_command(recorded: str) -> str | None:
    """Map a ``>`` history line to the shell command it ran, if any.

    ``/run`` and ``/test`` execute a shell command; other slash commands
    (``/add``, ``/model``…) do not. A bare ``>`` line is treated as a
    raw command.
    """
    for slash in _RUN_SLASH:
        if recorded == slash or recorded.startswith(slash + " "):
            return recorded[len(slash):].strip() or None
    if recorded.startswith("/"):
        return None
    return recorded or None


def _parse(text: str) -> tuple[list[tuple[str, str]], list[ParsedToolCall]]:
    pairs: list[tuple[str, str]] = []
    calls: list[ParsedToolCall] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("####"):
            pairs.append(("user", line[4:].strip()))
            continue
        if line.startswith("#"):
            continue  # "# aider chat started at …" and section headers
        if line.startswith(">"):
            command = _shell_command(line[1:].strip())
            if command is not None:
                n = len(calls) + 1
                calls.append(
                    ParsedToolCall(
                        tool_call_id=f"aider-cmd-{n}",
                        kind="execute",
                        status="unknown",  # history does not record exit codes
                        commands=(command,),
                        search_text=command.lower().replace("\\", "/"),
                        http_statuses=(),
                    )
                )
            continue
        pairs.append(("assistant", line))
    return pairs, calls


def load(path: str | Path, working_directory: str | None = None) -> SourceSession:
    """Load ``.aider.chat.history.md`` into the normalized audit view.

    ``working_directory`` defaults to the directory containing the
    history file — aider writes it at the repository root.
    """
    p = Path(path).expanduser()
    pairs, calls = _parse(p.read_text(encoding="utf-8", errors="replace"))
    session_id = f"aider:{p.name}"
    return SourceSession(
        source=SOURCE,
        session_id=session_id,
        title=p.name,
        working_directory=working_directory or str(p.parent),
        claims=tuple(claims_from_pairs(pairs, session_id)),
        calls=tuple(calls),
        raw_call_count=len(calls),
    )
