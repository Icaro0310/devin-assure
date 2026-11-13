"""Open-loop audit of the task ledger — devin-assure consumer surface.

Wraps ``devin_memory.ledger.integrations.audit``. The dependency is
optional (``devin-qa-pack[ledger]``); when ``devin-memory`` is not
installed this reports a clear message and exits 0 — a missing optional
integration is not an audit failure.
"""

from __future__ import annotations

import json
from typing import Any


def _import_ledger():
    try:
        from devin_memory.ledger import Ledger
        from devin_memory.ledger.integrations import audit
        return Ledger, audit
    except ImportError:
        return None, None


def run(parked_days: int = 7, ledger_dir: str | None = None) -> dict[str, Any]:
    """Return the audit dict, or ``{"error": ..., "install": ...}``."""
    Ledger, audit = _import_ledger()
    if Ledger is None:
        return {
            "error": "devin-memory not installed",
            "install": "pip install 'devin-qa-pack[ledger]'",
        }
    ledger = Ledger(ledger_dir) if ledger_dir else Ledger()
    return audit(ledger, parked_days=parked_days)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="devin-qa-pack ledger-audit")
    ap.add_argument("--parked-days", type=int, default=7,
                    help="parked tasks older than N days are overdue")
    ap.add_argument("--ledger-dir", default=None,
                    help="override ledger store path (default: XDG)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rep = run(a.parked_days, a.ledger_dir)
    if a.json or "error" in rep:
        print(json.dumps(rep, indent=2, sort_keys=True))
    else:
        c = rep["counts"]
        print(f"ledger audit: {rep['total']} tasks "
              f"(queued={c['queued']} active={c['active']} "
              f"parked={c['parked']} blocked={c['blocked']})")
        for t in rep["parked_overdue"]:
            print(f"  PARKED-overdue {t['id']} {t['age_days']}d — {t['title'][:60]}")
        for t in rep["blocked"]:
            print(f"  BLOCKED {t['id']} — {t['title'][:60]}")
        for t in rep["active"]:
            print(f"  ACTIVE {t['id']} — {t['title'][:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
