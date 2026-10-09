"""G3 harness: stats, scheduling, isolation, caps, report — all bridge-free.

Every test injects a fake ``runner``; nothing here needs DEVIN_BRIDGE_CMD.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
from devin_evals import abrun, g3stats

# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _write_task(tasks_dir: Path, ws_root: Path, tid: str, ttype: str,
                kind: str = "bugfix") -> Path:
    ws = ws_root / tid
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "seed.txt").write_text(f"seed {tid}\n", encoding="utf-8")
    (tasks_dir / f"{tid}.json").write_text(
        json.dumps({"id": tid, "kind": kind, "type": ttype,
                    "prompt": f"do {tid}", "workspace": str(ws)}),
        encoding="utf-8")
    return ws


@pytest.fixture
def tasks_dir(tmp_path: Path) -> Path:
    """A minimal valid suite: 5 trigger + 3 control manifests + workspaces."""
    d = tmp_path / "tasks"
    d.mkdir()
    ws_root = tmp_path / "ws"
    for i in range(5):
        _write_task(d, ws_root, f"trig-{i}", "trigger")
    for i in range(3):
        _write_task(d, ws_root, f"ctrl-{i}", "control")
    return d


def fake_runner(outcome):
    """Deterministic runner factory.

    ``outcome(unit) -> bool|dict``; bool means ok + fabricated session id.
    """

    def run(unit, repo, prompt, label, timeout_s):
        r = outcome(unit)
        if isinstance(r, dict):
            r.setdefault("ok", True)
            r.setdefault("session_id", f"s-{unit.task.id}-{unit.variant}-{unit.attempt}")
            return r
        return {"ok": bool(r), "session_id": f"s-{unit.task.id}-{unit.variant}-{unit.attempt}",
                "duration_s": 1.0, "denials": 0}

    return run


def _run(tasks_dir, tmp_path, runner, **kw):
    tasks = abrun.load_tasks(tasks_dir)
    kw.setdefault("work_dir", tmp_path / "work")
    return abrun.run_ab_v2(tasks, runner=runner, **kw)


# ---------------------------------------------------------------------------
# g3stats: Wilson, bootstrap, verdict truth table
# ---------------------------------------------------------------------------

class TestWilson:
    def test_known_value_8_of_10(self):
        lo, hi = g3stats.wilson_interval(8, 10)
        assert lo == pytest.approx(0.4902, abs=1e-3)
        assert hi == pytest.approx(0.9433, abs=1e-3)

    def test_zero_successes(self):
        lo, hi = g3stats.wilson_interval(0, 10)
        assert lo == 0.0
        assert hi == pytest.approx(0.2775, abs=1e-3)

    def test_empty_sample_is_widest(self):
        assert g3stats.wilson_interval(0, 0) == (0.0, 1.0)

    def test_bounds_within_unit_interval(self):
        lo, hi = g3stats.wilson_interval(10, 10)
        assert 0.0 <= lo < hi <= 1.0


class TestBootstrap:
    def test_reproducible_same_seed(self):
        deltas = [0.2, -0.1, 0.4, 0.0, 0.6]
        assert g3stats.bootstrap_ci(deltas, seed=7) == \
            g3stats.bootstrap_ci(deltas, seed=7)

    def test_seed_changes_draws(self):
        deltas = [0.2, -0.1, 0.4, 0.0, 0.6, -0.3]
        assert g3stats.bootstrap_ci(deltas, seed=7) != \
            g3stats.bootstrap_ci(deltas, seed=8)

    def test_constant_deltas_give_degenerate_ci(self):
        lo, hi = g3stats.bootstrap_ci([0.4, 0.4, 0.4], seed=1)
        assert lo == pytest.approx(0.4)
        assert hi == pytest.approx(0.4)

    def test_empty_returns_none(self):
        assert g3stats.bootstrap_ci([], seed=1) is None


class TestVerdict:
    """Truth table — every branch of the preregistered rule."""

    BASE: ClassVar[dict] = {"delta_ci": (-0.1, 0.1), "control_deltas": (), "denials_a": 0,
            "denials_b": 0, "baseline_rate": 0.5, "abort_rate_a": 0.0,
            "abort_rate_b": 0.0, "calibration_ok": True}

    def test_regresses_when_ci_below_zero(self):
        assert g3stats.verdict(**{**self.BASE, "delta_ci": (-0.5, -0.1)}) \
            == "regresses"

    def test_regresses_on_control_delta(self):
        assert g3stats.verdict(
            **{**self.BASE, "control_deltas": [0.0, -0.41]}) == "regresses"

    def test_control_delta_at_boundary(self):
        # -0.4 exactly also regresses (≤ threshold)
        assert g3stats.verdict(
            **{**self.BASE, "control_deltas": [-0.4]}) == "regresses"
        assert g3stats.verdict(
            **{**self.BASE, "control_deltas": [-0.39],
               "delta_ci": (-0.1, 0.1)}) == "no-detectable-effect"

    def test_regresses_on_safety_worsening(self):
        assert g3stats.verdict(**{**self.BASE, "denials_b": 2}) \
            == "regresses"

    def test_safety_not_worsened_when_baseline_had_denials(self):
        # rule only fires when denials_a == 0
        kw = {**self.BASE, "denials_a": 2, "denials_b": 3,
              "delta_ci": (-0.1, 0.1)}
        assert g3stats.verdict(**kw) == "no-detectable-effect"

    def test_safety_unknown_is_not_worsened(self):
        kw = {**self.BASE, "denials_a": None, "denials_b": None}
        assert g3stats.verdict(**kw) == "no-detectable-effect"

    def test_improves_needs_calibrated_positive_ci(self):
        kw = {**self.BASE, "delta_ci": (0.1, 0.4)}
        assert g3stats.verdict(**kw) == "improves"

    def test_improves_blocked_without_calibration(self):
        kw = {**self.BASE, "delta_ci": (0.1, 0.4), "calibration_ok": None}
        assert g3stats.verdict(**kw) == "inconclusive"
        kw["calibration_ok"] = False
        assert g3stats.verdict(**kw) == "inconclusive"

    def test_regress_beats_improve(self):
        kw = {**self.BASE, "delta_ci": (0.1, 0.4),
              "control_deltas": [-0.5]}
        assert g3stats.verdict(**kw) == "regresses"

    def test_no_detectable_effect(self):
        kw = {**self.BASE, "delta_ci": (-0.1, 0.15)}
        assert g3stats.verdict(**kw) == "no-detectable-effect"

    def test_wide_ci_is_inconclusive(self):
        kw = {**self.BASE, "delta_ci": (-0.2, 0.2)}
        assert g3stats.verdict(**kw) == "inconclusive"

    def test_ci_width_boundary(self):
        # exactly 0.30 width still counts as no-detectable-effect
        kw = {**self.BASE, "delta_ci": (-0.1, 0.2)}
        assert g3stats.verdict(**kw) == "no-detectable-effect"

    def test_baseline_ceiling(self):
        kw = {**self.BASE, "baseline_rate": 0.96}
        assert g3stats.verdict(**kw) == "inconclusive"
        kw["baseline_rate"] = 0.95
        assert g3stats.verdict(**kw) == "inconclusive"

    def test_baseline_floor(self):
        kw = {**self.BASE, "baseline_rate": 0.05}
        assert g3stats.verdict(**kw) == "inconclusive"
        kw["baseline_rate"] = 0.04
        assert g3stats.verdict(**kw) == "inconclusive"

    def test_too_many_aborts(self):
        kw = {**self.BASE, "abort_rate_a": 0.21}
        assert g3stats.verdict(**kw) == "inconclusive"
        kw = {**self.BASE, "abort_rate_b": 0.25,
              "delta_ci": (0.1, 0.4)}
        assert g3stats.verdict(**kw) == "inconclusive"

    def test_missing_delta_or_baseline(self):
        kw = {**self.BASE, "delta_ci": None}
        assert g3stats.verdict(**kw) == "inconclusive"
        kw = {**self.BASE, "baseline_rate": None}
        assert g3stats.verdict(**kw) == "inconclusive"


# ---------------------------------------------------------------------------
# tasks manifest + scheduling
# ---------------------------------------------------------------------------

class TestTasks:
    def test_load_tasks(self, tasks_dir):
        tasks = abrun.load_tasks(tasks_dir)
        assert len(tasks) == 8
        assert {t.type for t in tasks} == {"trigger", "control"}
        assert all(t.workspace.is_dir() for t in tasks)

    def test_ratio_validation(self, tmp_path):
        d = tmp_path / "tasks"
        d.mkdir()
        for i in range(4):  # only 4 trigger — below the ≥5 bar
            _write_task(d, tmp_path / "ws", f"trig-{i}", "trigger")
        for i in range(3):
            _write_task(d, tmp_path / "ws", f"ctrl-{i}", "control")
        tasks = abrun.load_tasks(d)
        with pytest.raises(abrun.G3Error, match="trigger"):
            abrun.validate_suite(tasks)

    def test_missing_workspace_fails(self, tmp_path):
        d = tmp_path / "tasks"
        d.mkdir()
        (d / "bad.json").write_text(json.dumps(
            {"id": "bad", "kind": "bugfix", "type": "trigger",
             "prompt": "x", "workspace": str(tmp_path / "nope")}))
        with pytest.raises(abrun.G3Error, match="workspace"):
            abrun.load_tasks(d)

    def test_bad_kind_fails(self, tmp_path):
        d = tmp_path / "tasks"
        d.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        (d / "bad.json").write_text(json.dumps(
            {"id": "bad", "kind": "nope", "type": "trigger",
             "prompt": "x", "workspace": str(ws)}))
        with pytest.raises(abrun.G3Error, match="kind"):
            abrun.load_tasks(d)

    def test_missing_dir_fails(self, tmp_path):
        with pytest.raises(abrun.G3Error, match="no such"):
            abrun.load_tasks(tmp_path / "absent")

    def test_index_manifest_layout(self, tmp_path):
        """The shipped tasks/ pack shape: one manifest.json, ``dir`` keys
        resolved against the manifest dir's parent."""
        tasks_root = tmp_path / "tasks"
        tasks_root.mkdir()
        entries = []
        for i in range(8):
            tid = f"t-{i}"
            ws = tasks_root / tid
            ws.mkdir()
            entries.append({"id": tid, "kind": "bugfix",
                            "type": "trigger" if i < 5 else "control",
                            "dir": f"tasks/{tid}", "prompt": f"do {tid}",
                            "grading": "pytest"})
        (tasks_root / "manifest.json").write_text(json.dumps(entries))
        tasks = abrun.load_tasks(tasks_root)
        assert len(tasks) == 8
        assert tasks[0].workspace == tasks_root / "t-0"
        trig, ctrl = abrun.validate_suite(tasks)
        assert (trig, ctrl) == (5, 3)

    def test_manifest_bad_entry_fails(self, tmp_path):
        d = tmp_path / "tasks"
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps(
            [{"id": "x", "kind": "nope", "type": "trigger",
              "dir": "tasks/x", "prompt": "p"}]))
        with pytest.raises(abrun.G3Error, match="kind"):
            abrun.load_tasks(d)


class TestPlan:
    def test_abba_baab_pattern_and_balanced_arms(self, tasks_dir):
        tasks = abrun.load_tasks(tasks_dir)
        units = abrun.plan_units(tasks, k=3, seed=1)
        assert len(units) == 8 * 2 * 3
        # group by task, in scheduled order
        order = []
        for u in units:
            if u.task.id not in order:
                order.append(u.task.id)
        for i, tid in enumerate(order):
            arms = [u.variant for u in units if u.task.id == tid]
            expected_first = "a" if i % 2 == 0 else "b"
            expected = "".join(
                expected_first if j % 4 in (0, 3)
                else ("b" if expected_first == "a" else "a")
                for j in range(6))
            assert "".join(arms) == expected
            assert arms.count("a") == arms.count("b") == 3
            assert sorted(u.attempt for u in units
                          if u.task.id == tid and u.variant == "a") == [0, 1, 2]

    def test_seeded_determinism(self, tasks_dir):
        tasks = abrun.load_tasks(tasks_dir)
        a = [(u.task.id, u.variant, u.attempt)
             for u in abrun.plan_units(tasks, 3, seed=42)]
        b = [(u.task.id, u.variant, u.attempt)
             for u in abrun.plan_units(tasks, 3, seed=42)]
        c = [(u.task.id, u.variant, u.attempt)
             for u in abrun.plan_units(tasks, 3, seed=43)]
        assert a == b
        assert a != c
        assert sorted(a) == sorted(c)  # same units, different order

    def test_attempts_below_min_rejected(self, tasks_dir):
        tasks = abrun.load_tasks(tasks_dir)
        with pytest.raises(abrun.G3Error):
            abrun.plan_units(tasks, k=2, seed=1)


# ---------------------------------------------------------------------------
# run_ab_v2: isolation, caps, aggregates, report
# ---------------------------------------------------------------------------

class TestRunV2:
    def test_copytree_isolation(self, tasks_dir, tmp_path):
        seen = []

        def runner(unit, repo, prompt, label, timeout_s):
            (repo / "ran.txt").write_text("x", encoding="utf-8")
            seen.append(str(repo))
            return {"ok": True, "session_id": "s1", "duration_s": 1.0,
                    "denials": 0}

        tasks = abrun.load_tasks(tasks_dir)
        work = tmp_path / "work"
        abrun.run_ab_v2(tasks, attempts=3, seed=1, work_dir=work,
                        runner=runner)
        # every attempt ran in a copy under work_dir — never the manifest ws
        assert seen and all(str(work) in s for s in seen)
        for t in tasks:
            assert not (t.workspace / "ran.txt").exists()
            assert (t.workspace / "seed.txt").read_text() == f"seed {t.id}\n"
        # copies are independent
        assert (work / "trig-0" / "a" / "attempt-0" / "ran.txt").exists()

    def test_solution_overlay_never_copied(self, tasks_dir, tmp_path):
        # _solution/ holds the reference fix — it must never reach an attempt
        for t in abrun.load_tasks(tasks_dir):
            sol = t.workspace / "_solution"
            sol.mkdir()
            (sol / "answer.py").write_text("# the fix\n")

        def runner(unit, repo, prompt, label, timeout_s):
            assert not (repo / "_solution").exists()
            return {"ok": True, "session_id": "s"}

        tasks = abrun.load_tasks(tasks_dir)
        abrun.run_ab_v2(tasks, attempts=3, seed=1,
                        work_dir=tmp_path / "w", runner=runner)

    def test_workspace_check_grader(self, tmp_path):
        import sys
        ws = tmp_path / "ws"
        (ws / "tests").mkdir(parents=True)
        (ws / "tests" / "test_ok.py").write_text(
            "def test_ok():\n    assert True\n")
        grade = abrun.make_workspace_check_grader(python=sys.executable)
        res = grade(None, {"workspace": str(ws)})
        assert res["success"] is True and res["checks_run"] == 1

        (ws / "tests" / "test_ok.py").write_text(
            "def test_ok():\n    assert False\n")
        res = grade(None, {"workspace": str(ws)})
        assert res["success"] is False and res["error"] is False

    def test_workspace_check_no_check_is_error(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        grade = abrun.make_workspace_check_grader()
        res = grade(None, {"workspace": str(ws)})
        assert res["error"] is True and res["success"] is False

    def test_labels_and_session_ids(self, tasks_dir, tmp_path):
        labels = []

        def runner(unit, repo, prompt, label, timeout_s):
            labels.append(label)
            return {"ok": True, "session_id": f"id-{label}",
                    "duration_s": 1.0, "denials": 0}

        tasks = abrun.load_tasks(tasks_dir)
        report = abrun.run_ab_v2(tasks, attempts=3, seed=1,
                                 work_dir=tmp_path / "w", runner=runner)
        assert all(l.startswith("g3-ab:") for l in labels)
        assert labels[0].count(":") == 3
        assert len(report["results"]["arm_a"]["sessions"]) > 0

    def test_max_sessions_cap_aborts_cleanly(self, tasks_dir, tmp_path):
        report = _run(tasks_dir, tmp_path,
                      fake_runner(lambda u: True),
                      attempts=3, seed=1, max_sessions=4)
        assert len(report["results"]["attempts"]) == 4
        assert "aborted early" in report["notes"]
        assert "max-sessions" in report["notes"]

    def test_max_total_time_cap(self, tasks_dir, tmp_path):
        ticks = iter([0, 0, 0, 999])  # t0, check1, check2, check3→abort
        report = _run(tasks_dir, tmp_path,
                      fake_runner(lambda u: True),
                      attempts=3, seed=1, max_total_time=60,
                      time_fn=lambda: next(ticks))
        assert len(report["results"]["attempts"]) == 2
        assert "max-total-time" in report["notes"]

    def test_failed_attempts_abort_and_never_retry(self, tasks_dir, tmp_path):
        calls = []

        def runner(unit, repo, prompt, label, timeout_s):
            calls.append((unit.task.id, unit.variant, unit.attempt))
            ok = not (unit.variant == "a" and unit.attempt >= 1)
            return {"ok": ok, "session_id": "s",
                    "error": None if ok else "boom"}

        tasks = abrun.load_tasks(tasks_dir)
        report = abrun.run_ab_v2(tasks, attempts=3, seed=1,
                                 work_dir=tmp_path / "w", runner=runner)
        a_recs = [r for r in report["results"]["attempts"]
                  if r["variant"] == "a"]
        aborted = [r for r in a_recs if r["aborted"]]
        assert len(aborted) == len(a_recs) - len(tasks)  # 2/3 of arm a
        assert all(not r["success"] for r in aborted)
        assert len(calls) == 48  # each unit ran exactly once, no retries
        assert report["verdict"] == "inconclusive"  # >20% aborts in arm a

    def test_report_schema_and_no_transcripts(self, tasks_dir, tmp_path):
        report = _run(tasks_dir, tmp_path,
                      fake_runner(lambda u: True),
                      attempts=3, seed=1)
        assert report["schema"] == "g3-report/0.1"
        for key in ("candidate", "environment", "design", "calibration",
                    "results", "verdict", "notes"):
            assert key in report
        d = report["design"]
        assert d["preregistered"] is True
        assert d["k"] == 3 and d["seed"] == 1
        assert d["trigger"] == 5 and d["control"] == 3
        assert "thresholds" in d and "caps" in d
        r = report["results"]
        for key in ("arm_a", "arm_b", "delta_trigger",
                    "control_regressions", "safety"):
            assert key in r
        assert report["environment"]["profile"] == "lab"
        blob = json.dumps(report)
        assert "do trig-0" not in blob  # no task/session content leaks
        assert "transcript" not in blob

    def test_delta_and_verdict_improves(self, tasks_dir, tmp_path):
        # arm B always passes; arm A passes 2/3 attempts → Δ = +1/3 per task
        # (grader decides success so no attempt aborts)
        def grader(unit, res):
            return {"success": unit.variant == "b" or unit.attempt < 2}

        report = _run(tasks_dir, tmp_path,
                      fake_runner(lambda u: True), grader=grader,
                      attempts=3, seed=1,
                      calibration={"aa_runs": 2,
                                   "aa_false_positive_rate": 0.0})
        delta = report["results"]["delta_trigger"]
        assert delta["mean"] == pytest.approx(1 / 3)
        lo, hi = delta["ci95"]
        assert lo > 0 <= hi
        assert report["verdict"] == "improves"

    def test_improves_blocked_without_calibration(self, tasks_dir, tmp_path):
        def grader(unit, res):
            return {"success": unit.variant == "b" or unit.attempt < 2}

        report = _run(tasks_dir, tmp_path,
                      fake_runner(lambda u: True), grader=grader,
                      attempts=3, seed=1)
        assert report["verdict"] == "inconclusive"
        assert report["calibration"] is None

    def test_aa_mode_fills_calibration(self, tasks_dir, tmp_path):
        # identical arms → Δ = 0 everywhere → NDE → fp rate 0
        def grader(unit, res):
            return {"success": unit.attempt < 2}

        report = _run(tasks_dir, tmp_path,
                      fake_runner(lambda u: True), grader=grader,
                      attempts=3, seed=1, aa=True,
                      variant_a="P", variant_b="Q")
        cal = report["calibration"]
        assert cal["aa_runs"] == 1
        assert cal["aa_false_positive_rate"] == 0.0
        assert report["verdict"] == "no-detectable-effect"
        assert report["candidate"]["kind"] == "aa-calibration"

    def test_aa_arm_b_uses_prefix_a(self, tasks_dir, tmp_path):
        prompts = []

        def runner(unit, repo, prompt, label, timeout_s):
            prompts.append((unit.variant, prompt.split("\n\n")[0]))
            return {"ok": True, "session_id": "s"}

        tasks = abrun.load_tasks(tasks_dir)
        abrun.run_ab_v2(tasks, attempts=3, seed=1, aa=True,
                        variant_a="AAA", variant_b="BBB",
                        work_dir=tmp_path / "w", runner=runner)
        assert set(prompts) == {("a", "AAA"), ("b", "AAA")}

    def test_grader_injection(self, tasks_dir, tmp_path):
        grades = []

        def grader(unit, res):
            grades.append(unit.task.id)
            return {"success": res.get("session_id") != "bad",
                    "tool_calls": 7}

        report = _run(tasks_dir, tmp_path,
                      fake_runner(lambda u: True),
                      grader=grader, attempts=3, seed=1)
        assert len(grades) == 48
        recs = report["results"]["attempts"]
        assert all(r["tool_calls"] == 7 for r in recs)
        assert all(r["success"] for r in recs)

    def test_baseline_ceiling_makes_inconclusive(self, tasks_dir, tmp_path):
        # everything passes → baseline 100% → cannot detect improvement
        report = _run(tasks_dir, tmp_path,
                      fake_runner(lambda u: True),
                      attempts=3, seed=1,
                      calibration={"aa_runs": 1,
                                   "aa_false_positive_rate": 0.0})
        assert report["verdict"] == "inconclusive"

    def test_candidate_file_sha256(self, tasks_dir, tmp_path):
        import hashlib
        cf = tmp_path / "skill.md"
        cf.write_bytes(b"skill-bytes")
        report = _run(tasks_dir, tmp_path,
                      fake_runner(lambda u: True),
                      attempts=3, seed=1,
                      candidate=abrun.candidate_block(
                          aa=False, prefix="", candidate_file=cf))
        assert report["candidate"]["sha256"] == \
            hashlib.sha256(b"skill-bytes").hexdigest()
        assert report["candidate"]["name"] == "skill.md"
