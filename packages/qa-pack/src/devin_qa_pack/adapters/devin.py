"""The Devin adapter — the native ``sessions.db`` source.

Claims come from ``message_nodes`` (agent-role text); ground truth comes
from ``tool_call_state`` rows decoded via devin-internals-spec parsers —
both through the devin-cli evidence provider.
"""

from __future__ import annotations

from dataclasses import replace

from devin_internals.parsers import SessionsStore
from devin_internals.parsers.sessions import Session

from devin_qa_pack.adapters.base import SourceSession, source_view
from devin_qa_pack.evidence.devin_cli import session_evidence


def session_view(store: SessionsStore, session: Session) -> SourceSession:
    """The store-backed view: bare session id + title (the legacy path —
    provider audits namespace ids as ``devin-cli:<id>`` instead)."""
    view = source_view(session_evidence(store, session), session.id)
    return replace(view, title=session.title)
