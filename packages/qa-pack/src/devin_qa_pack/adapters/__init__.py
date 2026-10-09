"""Source adapters: map an agent's transcript format into the audit model.

Every adapter produces a :class:`SourceSession` — the claims an agent
made plus the tool calls recorded as ground truth, normalized so the
same verifier runs over Devin stores and foreign transcripts alike.

- :mod:`devin_qa_pack.adapters.devin` — the native store
  (``sessions.db`` via devin-internals-spec).
- :mod:`devin_qa_pack.adapters.aider` — ``.aider.chat.history.md``
  (experimental).
- :mod:`devin_qa_pack.adapters.claude_code` — Claude Code ``.jsonl``
  session transcripts (experimental).
"""

from devin_qa_pack.adapters.base import SourceSession

__all__ = ["SourceSession"]
