"""Artifacts for a batch audit run (``audit --out PATH``).

The scheduled sweep complement to ``session_end``: ``audit --out PATH``
writes the full :func:`~devin_qa_pack.report.audits_payload` JSON to
``PATH`` (atomic tmp-file + replace, parents created) and appends one
compact verdict line per audited session to ``verdicts.jsonl`` next to
it — the same ledger ``session-end`` feeds, so hook verdicts and sweep
verdicts accumulate in one machine-readable file. Records carry
``origin: "sweep"`` so consumers can tell a batch audit apart from a
live SessionEnd verdict.

The store-isolation contract is identical to ``session-end``: every
write lands exactly where the caller points ``--out`` — never into
sessions.db or any other agent store (all providers open stores
read-only; this module performs the only file writes).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from devin_qa_pack.report import SessionAudit, audits_payload
from devin_qa_pack.session_end import _append_jsonl, _write_json

SWEEP_ORIGIN = "sweep"


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def sweep_record(audit: SessionAudit, audited_at: str) -> dict[str, Any]:
    """One compact ledger line — same keys as session-end's records."""
    counts = audit.counts()
    record: dict[str, Any] = {
        "session_id": audit.session_id,
        "verdict": audit.verdict,
        "audited_at": audited_at,
        "claims": len(audit.results),
        "verified": counts.get("verified", 0),
        "disputed": counts.get("disputed", 0),
        "unverifiable": counts.get("unverifiable", 0),
        "origin": SWEEP_ORIGIN,
    }
    if audit.surface:
        record["surface"] = audit.surface
    return record


def write_sweep_out(
    out: str | Path, audits: list[SessionAudit], label: str | Path
) -> Path:
    """Write the sweep payload to ``out`` + append per-session ledger lines.

    ``label`` is the audit's source label (the ``sessions_db`` field of
    the payload — a db path or surface description). Returns the path
    written. Raises ``OSError`` when the payload write fails — that file
    is the contract; the ledger append stays best-effort exactly like
    ``session-end``'s.
    """
    out_path = Path(out).expanduser()
    stamped = _utc_now()
    payload = audits_payload(audits, label)
    payload["audited_at"] = stamped
    # Ledger lives next to the payload — <out-dir>/verdicts.jsonl — so a
    # default <data-dir>/qa/ run feeds the same ledger as session-end,
    # and an --out override relocates both together (the append never
    # escapes the caller's chosen root). An --out naming an existing
    # verdicts.jsonl would clobber the ledger with a payload — refuse
    # before writing anything.
    verdicts = out_path.parent / "verdicts.jsonl"
    if verdicts == out_path and out_path.is_file():
        raise OSError(
            f"{out_path} is the verdict ledger — refusing to overwrite "
            "it with a sweep payload; choose a different --out name"
        )
    _write_json(out_path, payload)
    if verdicts != out_path:
        try:
            for audit in audits:
                _append_jsonl(verdicts, sweep_record(audit, stamped))
        except OSError:
            pass  # ledger is best-effort; the payload file is the contract
    return out_path
