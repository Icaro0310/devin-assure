"""Experimental Aider adapter — audits ``.aider.chat.history.md``.

Aider records its chat history as markdown (``io.append_chat_history``):
``####`` lines are user input — natural-language prompts *and* slash
commands (``#### /run pytest -q``, ``#### !ls``, ``#### /git status``);
``>`` lines are blockquoted tool/system output (``> Applied edit to
x.py``, ``> Commit abc1234 ...``, errors, confirmations); everything
else is assistant prose (the claim source).

Evidence is deliberately weak where the format is weak: a recorded
``/run``/``/test``/``!``/``/git`` line becomes an execute call with
``unknown`` status — the history does not persist exit codes — so
action claims like "tests passed" resolve ``unverifiable``, never a
fake ``verified``. The ``>`` output lines still corroborate
value-style claims: a recorded "47 passed" backs a ``tests_passed``
claim, a ``> Commit a1b2c3d`` line backs a ``commit`` claim even
without the repo on disk (``git log`` in the working directory stays
the stronger check). Commit claims also verify through the repo's own
``git log`` whenever the working directory is on disk, since aider
auto-commits.
"""

from __future__ import annotations

from pathlib import Path

from devin_qa_pack.adapters.base import SourceSession
from devin_qa_pack.claims import claims_from_pairs
from devin_qa_pack.verify import (
    ParsedToolCall,
    _coverage_values,
    _eval_scores,
    _http_statuses,
    _passed_counts,
)

SOURCE = "aider"

# Slash commands that actually run a shell command — ``/run``/``/test``
# shell out and ``/git X`` runs ``git X``. Everything else (``/add``,
# ``/model``, ``/commit``…) does not execute anything checkable; its
# evidence, if any, lands on the ``>`` output lines instead.
_RUN_SLASH = ("/run", "/test", "/git")


def _command_of(user_input: str) -> str | None:
    """Map a ``####`` input line to the shell command it ran, if any.

    ``/run pytest``, ``/test`` and the ``!cmd`` shell shortcut are
    commands; ``/git push`` records as ``git push``. A bare ``/test``
    falls back to aider's configured ``test_cmd`` — unknown here — so
    it records no command.
    """
    text = user_input.strip()
    if text.startswith("!"):
        command = text[1:].strip()
        return command or None
    for slash in _RUN_SLASH:
        if text == slash or text.startswith(slash + " "):
            rest = text[len(slash):].strip()
            if not rest:
                return None
            return f"git {rest}" if slash == "/git" else rest
    return None


def _parse(text: str) -> tuple[list[tuple[str, str]], list[ParsedToolCall]]:
    pairs: list[tuple[str, str]] = []
    calls: list[ParsedToolCall] = []
    output: list[str] = []  # ">" blockquote lines — recorded tool output
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("####"):
            user_input = line[4:].strip()
            command = _command_of(user_input)
            if command is not None:
                calls.append(
                    ParsedToolCall(
                        tool_call_id=f"aider-cmd-{len(calls) + 1}",
                        kind="execute",
                        status="unknown",  # history records no exit code
                        commands=(command,),
                        search_text=command.lower().replace("\\", "/"),
                        http_statuses=(),
                        passed_counts=(),  # no output on the command line
                    )
                )
            else:
                pairs.append(("user", user_input))
            continue
        if line.startswith("#"):
            continue  # "# aider chat started at …" and section headers
        if line.startswith(">"):
            output.append(line[1:].strip())
            continue
        pairs.append(("assistant", line))
    if output:
        # Aider's tool output never ties back to a specific command
        # (no ids, no exit codes) — pool it into one evidence call so
        # value claims (counts, statuses, percents, scores) and
        # file/commit details check against what was actually recorded.
        blob = "\n".join(output)
        calls.append(
            ParsedToolCall(
                tool_call_id="aider-output",
                kind="other",
                status="completed",
                commands=(),
                search_text=blob.lower().replace("\\", "/"),
                http_statuses=tuple(sorted(set(_http_statuses(blob)))),
                passed_counts=tuple(sorted(set(_passed_counts(blob)))),
                coverage_values=tuple(sorted(set(_coverage_values(blob)))),
                eval_scores=tuple(sorted(set(_eval_scores(blob)))),
            )
        )
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
