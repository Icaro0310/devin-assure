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

from devin_qa_pack import __version__
from devin_qa_pack.adapters.base import SourceSession, source_view
from devin_qa_pack.adapters.devin import session_view
from devin_qa_pack.evidence import SessionEvidence
from devin_qa_pack.verify import (
    DISPUTED,
    UNVERIFIABLE,
    VERIFIED,
    VerifiedClaim,
    verify_claims,
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
    surface: str | None = None
    warnings: list[str] = field(default_factory=list)

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


def audit_source(
    source: SourceSession,
    claim_limit: int | None = None,
    *,
    online: bool = False,
    allow_domains: tuple[str, ...] = (),
) -> SessionAudit:
    """Audit any adapter-produced view — the source-agnostic core."""
    claims = list(source.claims)
    if claim_limit is not None:
        claims = claims[: max(claim_limit, 0)]
    results = verify_claims(
        claims,
        list(source.calls),
        source.working_directory,
        raw_call_count=source.raw_call_count,
        online=online,
        allow_domains=allow_domains,
    )
    return SessionAudit(
        session_id=source.session_id,
        title=source.title,
        working_directory=source.working_directory,
        verdict=_verdict(results),
        results=results,
        surface=source.source,
    )


def audit_evidence(
    evidence: SessionEvidence,
    claim_limit: int | None = None,
    *,
    online: bool = False,
    allow_domains: tuple[str, ...] = (),
) -> SessionAudit:
    """Audit one provider-produced session — ids are namespaced as
    ``surface:native_id`` so multi-surface output stays unambiguous."""
    audit = audit_source(
        source_view(
            evidence, f"{evidence.surface}:{evidence.native_id}"
        ),
        claim_limit,
        online=online,
        allow_domains=allow_domains,
    )
    audit.warnings = list(evidence.warnings)
    return audit


def audit_session(
    store: SessionsStore,
    session: Session,
    claim_limit: int | None = None,
    *,
    online: bool = False,
    allow_domains: tuple[str, ...] = (),
) -> SessionAudit:
    return audit_source(
        session_view(store, session),
        claim_limit,
        online=online,
        allow_domains=allow_domains,
    )


def _is_within(path: Path, root: Path) -> bool:
    """True when ``path`` resolves to ``root`` or a directory under it."""
    try:
        path.expanduser().resolve().relative_to(root)
        return True
    except ValueError:
        return False


def audit_all(
    store: SessionsStore,
    limit: int | None = None,
    *,
    within: str | Path | None = None,
    online: bool = False,
    allow_domains: tuple[str, ...] = (),
) -> list[SessionAudit]:
    if within is None:
        sessions = store.sessions(limit=limit)
    else:
        # ``within`` filters on working_directory, so the SQL LIMIT cannot
        # apply first — the newest session overall is often another
        # project's, and a commit gate must audit *this* repo's newest.
        root = Path(within).expanduser().resolve()
        sessions = [
            s
            for s in store.sessions()
            if s.working_directory
            and _is_within(Path(s.working_directory), root)
        ][:limit]
    return [
        audit_session(store, s, online=online, allow_domains=allow_domains)
        for s in sessions
    ]


def filter_evidences(
    evidences: list[SessionEvidence],
    *,
    within: str | Path | None = None,
    limit: int | None = None,
) -> list[SessionEvidence]:
    """``--within``/``--limit`` scoping over provider sessions — same
    rule as the store path: within filters working_dir first, then cap."""
    if within is not None:
        root = Path(within).expanduser().resolve()
        evidences = [
            e
            for e in evidences
            if e.working_dir and _is_within(Path(e.working_dir), root)
        ]
    return evidences[:limit] if limit is not None else evidences


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
    out = {
        "session_id": audit.session_id,
        "surface": audit.surface,
        "title": audit.title,
        "working_directory": audit.working_directory,
        "verdict": audit.verdict,
        "summary": audit.counts(),
        "claims": [_claim_dict(r) for r in audit.results],
    }
    if audit.warnings:
        out["warnings"] = list(audit.warnings)
    return out


def audits_payload(
    audits: list[SessionAudit], sessions_db: str | Path
) -> dict[str, Any]:
    return {
        "version": __version__,
        "sessions_db": str(sessions_db),
        "sessions": [audit_dict(a) for a in audits],
    }


def render_audit(audit: SessionAudit) -> str:
    counts = audit.counts()
    title = f"  {audit.title}" if audit.title else ""
    lines = [
        (f"{audit.verdict}  {audit.session_id}{title} — "
         f"{len(audit.results)} claim(s): "
         f"{counts[VERIFIED]} verified, "
         f"{counts[DISPUTED]} disputed, "
         f"{counts[UNVERIFIABLE]} unverifiable")
    ]
    for r in audit.results:
        detail = r.claim.detail or "-"
        lines.append(
            f"  [{r.status:<12}] {r.claim.kind}/{detail}: {r.evidence}"
        )
    for warning in audit.warnings:
        lines.append(f"  [warning    ] {warning}")
    return "\n".join(lines)


def render_text(audits: list[SessionAudit]) -> str:
    return "\n".join(render_audit(a) for a in audits)
