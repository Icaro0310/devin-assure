"""The shipped ``evals/`` directory must grade the demo db as advertised."""

from __future__ import annotations

from pathlib import Path

from devin_evals.demo import build_demo_sessions_db
from devin_evals.runner import run_evals

EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"


def test_shipped_evals_against_demo_db(tmp_path):
    db = build_demo_sessions_db(tmp_path / "demo.db")
    report = run_evals(EVALS_DIR, db)
    statuses = {c["id"]: c["status"] for c in report["cases"]}
    assert statuses == {
        "demo-pass": "pass",
        "demo-fail": "fail",
        "demo-toolcall": "pass",
    }
    assert report["summary"]["passed"] == 2
    assert report["summary"]["failed"] == 1


def test_demo_db_has_ground_truth(tmp_path):
    """tool_called must see names — the differentiator only works if the
    demo db records structured tool_call_state."""
    db = build_demo_sessions_db(tmp_path / "demo.db")
    report = run_evals(EVALS_DIR, db)
    case = next(c for c in report["cases"] if c["id"] == "demo-toolcall")
    check = next(c for c in case["checks"] if c["grader"] == "tool_called")
    assert "1/1" in check["detail"]
