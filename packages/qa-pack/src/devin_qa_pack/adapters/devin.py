"""The Devin adapter — the native ``sessions.db`` source.

Claims come from ``message_nodes`` (agent-role text); ground truth comes
from ``tool_call_state`` rows decoded via devin-internals-spec parsers.
"""

from __future__ import annotations

from devin_internals.parsers import SessionsStore
from devin_internals.parsers.sessions import Session

from devin_qa_pack.adapters.base import SourceSession
from devin_qa_pack.claims import extract_claims
from devin_qa_pack.verify import parse_tool_calls


def session_view(store: SessionsStore, session: Session) -> SourceSession:
    nodes = store.message_nodes(session.id)
    states = store.tool_call_state(session.id)
    return SourceSession(
        source="devin",
        session_id=session.id,
        title=session.title,
        working_directory=session.working_directory,
        claims=tuple(extract_claims(nodes)),
        calls=tuple(parse_tool_calls(states)),
        raw_call_count=len(states),
    )
