"""Per-session audit verdicts.

A session audit extracts every deliverable claim from its message_nodes,
verifies each against tool_call_state (+ git when the working directory
is on disk) and collapses the results into one verdict:

- ``PASS``       — at least one claim, all verified.
- ``PARTIAL``    — any dispute or any mix of outcomes (including fully
  disputed sessions; the findings list says which claims failed).
- ``UNVERIFIED`` — no claims found, or none could be checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devin_internals.parsers import SessionsStore
from devin_internals.parsers.sessions import Session

from devin_qa_pack.claims import URL, extract_claims
from devin_qa_pack.verify import (
    DISPUTED,
    UNVERIFIABLE,
    VERIFIED,
    VerifiedClaim,
    parse_tool_calls,
    verify_claim,
)

PASS = "PASS"
PARTIAL = "PARTIAL"
UNVERIFIED = "UNVERIFIED"

_STATUSES = (VERIFIED, DISPUTED, UNVERIFIABLE)


@dataclass
class SessionAudit:
    session_id: str
    title: str | None
    working_directory: str
    verdict: str
    results: list[VerifiedClaim] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            s: sum(1 for r in self.results if r.status == s) for s in _STATUSES
        }


def _verdict(results: list[VerifiedClaim]) -> str:
    if not results:
        return UNVERIFIED
    statuses = {r.status for r in results}
    if statuses == {VERIFIED}:
        return PASS
    if statuses == {UNVERIFIABLE}:
        return UNVERIFIED
    return PARTIAL


def audit_session(
    store: SessionsStore,
    session: Session,
    claim_limit: int | None = None,
    *,
    online: bool = False,
    allow_domains: tuple[str, ...] = (),
) -> SessionAudit:
    nodes = store.message_nodes(session.id)
    states = store.tool_call_state(session.id)
    calls = parse_tool_calls(states)
    claims = extract_claims(nodes)
    if claim_limit is not None:
        claims = claims[: max(claim_limit, 0)]
    results = [
        _verify_url(c, online, allow_domains)
        if c.kind == URL
        else verify_claim(
            c,
            calls,
            session.working_directory,
            raw_call_count=len(states),
        )
        for c in claims
    ]
    return SessionAudit(
        session_id=session.id,
        title=session.title,
        working_directory=session.working_directory,
        verdict=_verdict(results),
        results=results,
    )


def audit_all(
    store: SessionsStore,
    limit: int | None = None,
    *,
    online: bool = False,
    allow_domains: tuple[str, ...] = (),
) -> list[SessionAudit]:
    return [
        audit_session(store, s, online=online, allow_domains=allow_domains)
        for s in store.sessions(limit=limit)
    ]


def _claim_dict(r: VerifiedClaim) -> dict[str, Any]:
    return {
        "kind": r.claim.kind,
        "detail": r.claim.detail,
        "status": r.status,
        "evidence": r.evidence,
        "node_id": r.claim.node_id,
        "excerpt": r.claim.excerpt,
    }


def audit_dict(audit: SessionAudit) -> dict[str, Any]:
    return {
        "session_id": audit.session_id,
        "title": audit.title,
        "working_directory": audit.working_directory,
        "verdict": audit.verdict,
        "summary": audit.counts(),
        "claims": [_claim_dict(r) for r in audit.results],
    }


def audits_payload(
    audits: list[SessionAudit], sessions_db: str | Path
) -> dict[str, Any]:
    return {
        "sessions_db": str(sessions_db),
        "sessions": [audit_dict(a) for a in audits],
    }


def render_audit(audit: SessionAudit) -> str:
    counts = audit.counts()
    title = f"  {audit.title}" if audit.title else ""
    lines = [
        f"{audit.verdict}  {audit.session_id}{title} — "
        f"{len(audit.results)} claim(s): "
        f"{counts[VERIFIED]} verified, "
        f"{counts[DISPUTED]} disputed, "
        f"{counts[UNVERIFIABLE]} unverifiable"
    ]
    for r in audit.results:
        detail = r.claim.detail or "-"
        lines.append(
            f"  [{r.status:<12}] {r.claim.kind}/{detail}: {r.evidence}"
        )
    return "\n".join(lines)


def render_text(audits: list[SessionAudit]) -> str:
    return "\n".join(render_audit(a) for a in audits)
