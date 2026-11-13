"""Integration coverage for the sibling ``devin-review-fallback`` repo.

The reusable pipeline (``classify -> static-scan + judge -> coverage``)
lives in its own checkout next to ``devin-assure`` — new consumer repos
are expected to wire it in via ``examples/caller.yml``, so the pack pins
the contract those callers rely on:

- ``classify-devin-status.py`` must read the commit-**status** surface
  (Devin Review never posts a check run) and distinguish a real analysis
  from a credit-exhausted skip — both post ``state: success``, only the
  ``description`` differs. Exercised here as a subprocess against a stub
  ``gh`` serving canned ``/status`` payloads; nothing hits the network.
- ``publish-review-coverage.py`` must turn the judge verdict into an
  honest outcome: approve is ``success``/``COVERED``, reject is
  ``failure`` (terminal — advisory evidence cannot clear it), a gate
  error is ``action_required``, and a verdict still in flight stays
  ``in_progress``/``pending`` — pending is not a failure; the
  laptop-side watcher completes the check when ``devin-judge/review``
  lands.
- ``ai-review-fallback.yml`` must keep ``static-scan`` advisory (it
  never gates coverage) while ``judge`` — the decider — polls the
  ``devin-judge/review`` commit status posted by the watcher. Lane A
  (Ollama ``review-a``/``review-b``) was retired 2026-11: a 1.5B model
  is an intern for static data, not a PR decider.

Resolution order for the checkout: ``$DEVIN_REVIEW_FALLBACK_ROOT``, then
``<devin-assure>/../devin-review-fallback``. The whole module skips when
neither exists (e.g. a bare devin-assure CI checkout).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

FALLBACK_ROOT = Path(
    os.environ.get(
        "DEVIN_REVIEW_FALLBACK_ROOT",
        Path(__file__).resolve().parents[4] / "devin-review-fallback",
    )
)
CLASSIFY_SCRIPT = FALLBACK_ROOT / "scripts" / "classify-devin-status.py"
COVERAGE_SCRIPT = FALLBACK_ROOT / "scripts" / "publish-review-coverage.py"
WORKFLOW = FALLBACK_ROOT / ".github" / "workflows" / "ai-review-fallback.yml"
CALLER = FALLBACK_ROOT / "examples" / "caller.yml"
BASH = shutil.which("bash")
SHA = "0" * 40

pytestmark = [
    pytest.mark.skipif(
        not CLASSIFY_SCRIPT.is_file(),
        reason="devin-review-fallback sibling checkout not present "
        "(set DEVIN_REVIEW_FALLBACK_ROOT)",
    ),
    pytest.mark.skipif(BASH is None, reason="gh stub needs bash"),
]

GH_STUB = """#!/usr/bin/env bash
# Records every call in $GH_STUB_LOG, one "CALL <argv>" line each, and
# serves the canned JSON in $GH_STUB_RESPONSE (when set). Exits
# $GH_STUB_RC (default 0) — nonzero models an API failure.
printf 'CALL %s\\n' "$*" >> "${GH_STUB_LOG:-/dev/null}"
if [ -f "${GH_STUB_RESPONSE:-/nonexistent}" ]; then
  cat "$GH_STUB_RESPONSE"
fi
exit "${GH_STUB_RC:-0}"
"""


def _install_gh_stub(tmp_path: Path) -> Path:
    """Drop the stub ``gh`` on ``tmp_path/bin``; return the call log."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "gh"
    stub.write_text(GH_STUB, encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "gh-calls.log"
    log.touch()
    return log


def _env(tmp_path: Path, extra: dict[str, str] | None = None) -> dict:
    env = {
        "PATH": os.pathsep.join(
            (str(tmp_path / "bin"), os.environ.get("PATH", "/usr/bin:/bin"))
        ),
        "GH_TOKEN": "test-token",
        "HOME": str(tmp_path),
    }
    env.update(extra or {})
    return env


def _run(
    script: Path, args: list[str], env: dict
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _stdout_key(proc: subprocess.CompletedProcess, key: str) -> str:
    assert proc.returncode == 0, proc.stderr
    line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith(f"{key}=")),
        None,
    )
    assert line is not None, proc.stdout
    return line.split("=", 1)[1]


# ---------------------------------------------------------------------
# classify-devin-status.py — stubbed-gh classification
# ---------------------------------------------------------------------


def _devin_status(state: str = "success", description: str = "") -> dict:
    """A status entry that passes the script's identity pinning:
    context name + devin-app avatar marker + app.devin.ai target."""
    return {
        "context": "Devin Review",
        "state": state,
        "description": description,
        "target_url": "https://app.devin.ai/review/abc123",
        "avatar_url": "https://avatars.githubusercontent.com/in/811515",
    }


def _run_classify(
    tmp_path: Path,
    statuses: list[dict] | None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run the classifier with ``gh`` stubbed to serve ``statuses``
    (``None`` leaves the stub without a response — the exit code is then
    driven by ``GH_STUB_RC``). ``--wait-seconds 0`` bounds the poll loop
    to a single iteration."""
    log = _install_gh_stub(tmp_path)
    env = {"GH_STUB_LOG": str(log)}
    if statuses is not None:
        fixture = tmp_path / "statuses.json"
        fixture.write_text(
            json.dumps({"statuses": statuses}), encoding="utf-8"
        )
        env["GH_STUB_RESPONSE"] = str(fixture)
    env.update(extra_env or {})
    return _run(
        CLASSIFY_SCRIPT,
        [
            "--repo",
            "owner/repo",
            "--sha",
            SHA,
            "--wait-seconds",
            "0",
        ],
        _env(tmp_path, env),
    )


def _classify(tmp_path: Path, statuses: list[dict] | None, **extra) -> str:
    return _stdout_key(_run_classify(tmp_path, statuses, extra or None), "state")


def test_classify_executed(tmp_path: Path) -> None:
    """``success`` + 'completed analysis' description is real coverage."""
    assert (
        _classify(
            tmp_path,
            [
                _devin_status(
                    description=(
                        "Devin Review completed analysis "
                        "of this pull request"
                    )
                )
            ],
        )
        == "EXECUTED"
    )


@pytest.mark.parametrize(
    "marker", ["skipped", "no credits", "trial expired"]
)
def test_classify_skipped_credits(tmp_path: Path, marker: str) -> None:
    """A green status whose description carries a skip marker must NOT
    count as executed — that ambiguity is the bug this sensor exists
    for. 'trial expired' is the observed trial-exhaustion form."""
    desc = f"Devin Review {marker} for this pull request"
    assert _classify(tmp_path, [_devin_status(description=desc)]) == (
        "SKIPPED_CREDITS"
    )


@pytest.mark.parametrize("state", ["failure", "error"])
def test_classify_failed_before_review(tmp_path: Path, state: str) -> None:
    assert (
        _classify(tmp_path, [_devin_status(state=state)])
        == "FAILED_BEFORE_REVIEW"
    )


def test_classify_pending_is_unverified(tmp_path: Path) -> None:
    """Pending is not affirmative evidence either way."""
    assert (
        _classify(tmp_path, [_devin_status(state="pending")])
        == "UNVERIFIED"
    )


def test_classify_forged_context_is_unverified(tmp_path: Path) -> None:
    """Anyone with write access can set ``context: Devin Review`` — a
    status missing the app avatar marker must be ignored, not trusted."""
    forged = _devin_status(description="completed analysis")
    forged["avatar_url"] = "https://avatars.githubusercontent.com/u/1"
    assert _classify(tmp_path, [forged]) == "UNVERIFIED"


def test_classify_off_app_target_url_is_unverified(tmp_path: Path) -> None:
    forged = _devin_status(description="completed analysis")
    forged["target_url"] = "https://evil.example.test/review"
    assert _classify(tmp_path, [forged]) == "UNVERIFIED"


def test_classify_unrecognized_success_is_unverified(tmp_path: Path) -> None:
    """A green Devin status with a description the classifier does not
    recognize must fail safe — vacuous greens are why this exists."""
    assert (
        _classify(tmp_path, [_devin_status(description="all good")])
        == "UNVERIFIED"
    )


def test_classify_no_statuses_is_unverified(tmp_path: Path) -> None:
    assert _classify(tmp_path, []) == "UNVERIFIED"


def test_classify_latest_devin_status_wins(tmp_path: Path) -> None:
    """Statuses arrive newest-first; an older EXECUTED must not mask a
    newer skip on the same head SHA."""
    statuses = [
        _devin_status(description="trial expired"),
        _devin_status(description="completed analysis"),
    ]
    assert _classify(tmp_path, statuses) == "SKIPPED_CREDITS"


def test_classify_reads_status_surface_not_check_runs(
    tmp_path: Path,
) -> None:
    """Devin Review posts a commit status — polling ``/check-runs``
    would see nothing and classify every PR UNVERIFIED."""
    log = _install_gh_stub(tmp_path)
    fixture = tmp_path / "statuses.json"
    fixture.write_text(json.dumps({"statuses": []}), encoding="utf-8")
    _run(
        CLASSIFY_SCRIPT,
        ["--repo", "owner/repo", "--sha", SHA, "--wait-seconds", "0"],
        _env(
            tmp_path,
            {"GH_STUB_LOG": str(log), "GH_STUB_RESPONSE": str(fixture)},
        ),
    )
    calls = log.read_text(encoding="utf-8")
    assert "/status" in calls
    assert "check-runs" not in calls


def test_classify_gh_failure_degrades_to_unverified(tmp_path: Path) -> None:
    """API errors must land on the fallback side, never crash."""
    assert _classify(tmp_path, None, GH_STUB_RC="1") == "UNVERIFIED"


def test_classify_missing_gh_is_unverified(tmp_path: Path) -> None:
    """No ``gh`` on PATH at all — OSError inside the script degrades to
    UNVERIFIED (rc 0), it must not bubble up."""
    proc = _run(
        CLASSIFY_SCRIPT,
        ["--repo", "owner/repo", "--sha", SHA, "--wait-seconds", "0"],
        # PATH without the stub dir and without any real gh: only the
        # interpreter's own dir needs to resolve.
        {
            "PATH": "/nonexistent",
            "GH_TOKEN": "test-token",
            "HOME": str(tmp_path),
        },
    )
    assert _stdout_key(proc, "state") == "UNVERIFIED"


def test_classify_writes_github_output(tmp_path: Path) -> None:
    """The workflow reads ``steps.c.outputs.state`` — the value must
    reach GITHUB_OUTPUT, not only stdout."""
    out = tmp_path / "github-output"
    _run_classify(
        tmp_path,
        [_devin_status(description="completed analysis")],
        {"GITHUB_OUTPUT": str(out)},
    )
    assert "state=EXECUTED" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------
# publish-review-coverage.py — verdict table + posted surfaces
# ---------------------------------------------------------------------


def _run_publish(
    tmp_path: Path,
    args: list[str],
    extra_env: dict[str, str] | None = None,
    *,
    stub: bool = False,
) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the coverage publisher; returns ``(proc, gh_call_log)``. The
    stub is only installed when ``stub=True`` — ``--dry-run`` runs prove
    no ``gh`` call happens by leaving the log absent."""
    env = {}
    log = tmp_path / "gh-calls.log"
    if stub:
        log = _install_gh_stub(tmp_path)
        env["GH_STUB_LOG"] = str(log)
    env.update(extra_env or {})
    proc = _run(
        COVERAGE_SCRIPT,
        ["--repo", "owner/repo", "--sha", SHA, *args],
        _env(tmp_path, env),
    )
    return proc, log


@pytest.mark.parametrize(
    ("devin", "a", "b", "verdict", "status", "conclusion"),
    [
        # primary reviewer ran — fallbacks skipped, still covered
        ("EXECUTED", "SKIPPED", "SKIPPED", "EXECUTED", "completed",
         "success"),
        # devin=EXECUTED wins even over a queued judge reject — the
        # primary review already covered this head
        ("EXECUTED", "DISABLED", "REJECTED", "EXECUTED", "completed",
         "success"),
        # judge approved — coverage is judge-only since lane A retired
        ("UNVERIFIED", "DISABLED", "EXECUTED", "COVERED", "completed",
         "success"),
        ("SKIPPED_CREDITS", "DISABLED", "EXECUTED", "COVERED",
         "completed", "success"),
        # judge rejected — terminal; advisory evidence cannot clear it
        ("UNVERIFIED", "EXECUTED", "REJECTED", "FAILURE", "completed",
         "failure"),
        # judge ran and errored — a human decides, nothing is approved
        ("UNVERIFIED", "DISABLED", "ERROR", "ACTION_REQUIRED",
         "completed", "action_required"),
        ("FAILED_BEFORE_REVIEW", "DISABLED", "ERROR", "ACTION_REQUIRED",
         "completed", "action_required"),
        # no judge verdict yet — pending is not a failure: the watcher
        # completes the check run when devin-judge/review lands
        ("UNVERIFIED", "DISABLED", "UNAVAILABLE", "PENDING",
         "in_progress", "None"),
        ("SKIPPED_CREDITS", "DISABLED", "SKIPPED", "PENDING",
         "in_progress", "None"),
        ("FAILED_BEFORE_REVIEW", "DISABLED", "UNAVAILABLE", "PENDING",
         "in_progress", "None"),
    ],
)
def test_publish_verdict_table(
    tmp_path: Path,
    devin: str,
    a: str,
    b: str,
    verdict: str,
    status: str,
    conclusion: str,
) -> None:
    proc, _ = _run_publish(
        tmp_path,
        ["--devin", devin, "--a", a, "--b", b, "--dry-run"],
    )
    assert _stdout_key(proc, "verdict") == verdict
    assert _stdout_key(proc, "status") == status
    # in_progress runs carry no conclusion — the script prints "None"
    assert _stdout_key(proc, "conclusion") == conclusion


def test_publish_dry_run_posts_nothing(tmp_path: Path) -> None:
    """``--dry-run`` must not reach ``gh`` — install the stub and prove
    the call log stays empty."""
    log = _install_gh_stub(tmp_path)
    proc = _run(
        COVERAGE_SCRIPT,
        [
            "--repo",
            "owner/repo",
            "--sha",
            SHA,
            "--devin",
            "UNVERIFIED",
            "--a",
            "EXECUTED",
            "--b",
            "FAILED",
            "--dry-run",
        ],
        _env(tmp_path, {"GH_STUB_LOG": str(log)}),
    )
    assert proc.returncode == 0, proc.stderr
    assert log.read_text(encoding="utf-8") == ""


def test_publish_blocking_findings_stay_success(tmp_path: Path) -> None:
    """A review that ran and found criticals is successful execution —
    findings are data for the gate, not a verdict change."""
    proc, _ = _run_publish(
        tmp_path,
        [
            "--devin",
            "UNVERIFIED",
            "--a",
            "EXECUTED",
            "--b",
            "EXECUTED",
            "--a-critical",
            "2",
            "--dry-run",
        ],
    )
    assert _stdout_key(proc, "conclusion") == "success"
    assert "findings=blocking" in _stdout_key(proc, "description")


def test_publish_error_posts_action_required_check_and_pending_status(
    tmp_path: Path,
) -> None:
    """b=ERROR is terminal but not a code failure: the published
    surfaces must agree on 'not green' — the check run (gate's source
    of truth) completes ``action_required`` and the commit-status
    mirror is ``pending``, since statuses have no action_required."""
    proc, log = _run_publish(
        tmp_path,
        ["--devin", "UNVERIFIED", "--a", "DISABLED", "--b", "ERROR"],
        stub=True,
    )
    assert proc.returncode == 0, proc.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    check = next(c for c in calls if "check-runs" in c)
    status = next(c for c in calls if "/statuses/" in c)
    assert "status=completed" in check
    assert "conclusion=action_required" in check
    assert "name=Review Coverage" in check
    assert "ACTION_REQUIRED" in check
    assert "state=pending" in status
    assert "context=Review Coverage" in status


def test_publish_pending_verdict_posts_in_progress_check(
    tmp_path: Path,
) -> None:
    """A judge verdict still in flight is pending, not a failure: the
    check run stays ``in_progress`` with NO conclusion field — the
    watcher completes it when ``devin-judge/review`` lands — and the
    status mirror is ``pending``. Posting a terminal conclusion here
    would either fake coverage (success) or blame the code (failure)."""
    proc, log = _run_publish(
        tmp_path,
        ["--devin", "UNVERIFIED", "--a", "DISABLED", "--b", "UNAVAILABLE"],
        stub=True,
    )
    assert proc.returncode == 0, proc.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    check = next(c for c in calls if "check-runs" in c)
    status = next(c for c in calls if "/statuses/" in c)
    assert "status=in_progress" in check
    assert "conclusion=" not in check
    assert "name=Review Coverage" in check
    assert "state=pending" in status
    assert "context=Review Coverage" in status


def test_publish_failure_to_post_check_run_exits_1(tmp_path: Path) -> None:
    """If the check run cannot be posted the job must fail — a silent
    pass would leave the gate with no evidence to read."""
    proc, _ = _run_publish(
        tmp_path,
        ["--devin", "UNVERIFIED", "--a", "EXECUTED", "--b", "EXECUTED"],
        extra_env={"GH_STUB_RC": "1"},
        stub=True,
    )
    assert proc.returncode == 1


# ---------------------------------------------------------------------
# ai-review-fallback.yml + caller — static contract
# ---------------------------------------------------------------------


def _load_workflow() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # YAML 1.1 parses the literal key `on` as boolean True.
    wf["on"] = wf.pop(True)
    return wf


def _jobs() -> dict:
    return _load_workflow()["jobs"]


def test_workflow_is_reusable_only() -> None:
    """Callers choose their trigger; the reusable must not self-fire."""
    assert set(_load_workflow()["on"]) == {"workflow_call"}


def test_pipeline_shape() -> None:
    assert {"classify", "static-scan", "judge", "coverage"} <= set(_jobs())


def test_retired_reviewer_lanes_are_gone() -> None:
    """Lane A (Ollama) was retired 2026-11 — a 1.5B model is advisory
    for static data, never a PR decider; the ``review-a``/``review-b``
    job names must not reappear under the retired contract."""
    assert "review-a" not in _jobs()
    assert "review-b" not in _jobs()


def test_fallback_lanes_wait_for_classify() -> None:
    """The fallback lanes only pay for a PR the primary reviewer did
    not cover — they must gate on the classifier's output, and
    ``always()`` keeps their outcomes honest when classify itself had
    a rough ride."""
    for job in ("static-scan", "judge"):
        assert _jobs()[job]["needs"] == "classify"
        assert "always()" in _jobs()[job]["if"]
        assert "needs.classify.outputs.state != 'EXECUTED'" in _jobs()[
            job
        ]["if"]


def test_judge_does_not_depend_on_static_scan() -> None:
    """static-scan is advisory evidence only — the judge (the decider)
    must not wait on it, or a stuck scan would pin the verdict."""
    assert _jobs()["judge"]["needs"] == "classify"


def test_coverage_collects_every_leg() -> None:
    """Coverage waits on classify + judge only — static-scan is
    advisory and must never gate the verdict."""
    job = _jobs()["coverage"]
    assert sorted(job["needs"]) == ["classify", "judge"]
    assert "always()" in job["if"]


def test_coverage_can_publish_both_surfaces() -> None:
    """The verdict is a check run (action_required exists there) mirrored
    to a commit status (status-event retriggers) — dropping either
    permission silently unpublishes the verdict."""
    perms = _jobs()["coverage"]["permissions"]
    assert perms["checks"] == "write"
    assert perms["statuses"] == "write"


def test_lane_jobs_are_bounded() -> None:
    """An unbounded lane pins the coverage gate for the 6h platform
    default when a scanner or the judge poll sticks. The judge timeout
    must stay comfortably above the ``judge-wait-minutes`` input
    default (5) or the poll deadline never fires."""
    assert _jobs()["static-scan"]["timeout-minutes"] == 15
    assert _jobs()["judge"]["timeout-minutes"] == 30


def test_every_action_is_sha_pinned() -> None:
    """Third-party actions (and the model-facing reviewers especially)
    must be pinned to a full commit SHA — a movable tag is a supply-
    chain hole in a workflow that runs on pull_request_target."""
    uses = [
        step["uses"]
        for job in _jobs().values()
        for step in job.get("steps", [])
        if "uses" in step
    ]
    assert uses, "expected at least one action reference"
    for ref in uses:
        assert re.search(r"@[0-9a-f]{40}$", ref), ref


def test_caller_triggers_on_pull_request_target() -> None:
    """The shipped example is the template consumers copy — it must
    trigger on pull_request_target so the pipeline files come from the
    base branch and a PR cannot alter its own review."""
    caller = yaml.safe_load(CALLER.read_text(encoding="utf-8"))
    on = caller.get("on", caller.get(True))
    assert "pull_request_target" in on


def test_caller_uses_the_reusable_workflow() -> None:
    caller = yaml.safe_load(CALLER.read_text(encoding="utf-8"))
    uses = caller["jobs"]["fallback"]["uses"]
    assert "devin-review-fallback/.github/workflows/ai-review-fallback.yml" in uses
