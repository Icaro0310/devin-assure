"""Static assertions for .github/workflows/auto-approve.yml.

The caller is thin by design (triggers + pin to the reusable
pr-approve gate in devin-powerups). These tests pin the wiring that
makes the v1.4 fallback reachable: the gate version and the
pull_request_review_comment trigger that lets a late fix-commit reply
re-evaluate the gate without waiting for a push.
"""

from pathlib import Path

import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "auto-approve.yml"
)
GATE_PIN = "Icaro0310/devin-powerups/.github/workflows/pr-approve.yml@"


def _load() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    wf["on"] = wf.pop(True)
    return wf


def test_review_comment_trigger_re_evaluates_gate() -> None:
    """Replies citing fix commits must re-run the gate, not wait for push."""
    assert _load()["on"]["pull_request_review_comment"]["types"] == ["created"]


def test_gate_pin_is_v1_9() -> None:
    """v1.9 fixes the self-cancel so a successful approval no longer
    renders as a failed check, plus fresh-head recheck and paginated
    dedup; the pin must be exact so a silent downgrade is caught here."""
    job = _load()["jobs"]["approve"]
    uses = job["uses"]
    assert uses == GATE_PIN + "v1.9"
