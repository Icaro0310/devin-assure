"""EV-5/G3: A/B eval — run two variants in fresh bridge sessions, grade both.

v1 (:func:`run_ab`, kept for ``ab-run --task``) creates exactly two
sessions. v2 (:func:`run_ab_v2`) is the G3 harness: a manifest suite of
≥5 trigger + ≥3 control tasks, k attempts per arm, seeded ABBA/BAAB
interleaving, budget caps, preregistered verdicts and a ``g3-report/0.1``
JSON report.

Honest cost note: this creates real sessions and consumes tokens; it is
opt-in by design and every session is labelled ``g3-ab:<task>:<variant>:<attempt>``
(v1: ``ab-run:<tag>:<variant>``) so ``devin-janitor`` can reap the noise.
Fail-closed without ``DEVIN_BRIDGE_CMD`` (see judge.py).

Everything except the bridge call itself is injectable — ``run_ab_v2``
takes a ``runner`` callable (and an optional ``grader``), so ordering,
isolation, caps and statistics are unit-tested with fakes, no bridge.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from devin_evals import g3stats
from devin_evals.judge import judge_available

DEFAULT_TIMEOUT_S = 45 * 60


def _bridge_prompt(bridge: str, repo: str, prompt: str, label: str,
                   timeout_s: int) -> dict[str, Any]:
    """One bridge session + prompt; returns {ok, session_id?, error?, ...}."""
    argv = ["node", bridge, "prompt", repo, prompt,
            "--label", label, "--yes",
            "--timeout-ms", str(timeout_s * 1000)]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_s + 30)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "bridge prompt timed out",
                "duration_s": time.monotonic() - t0, "raw": ""}
    except OSError as e:
        return {"ok": False, "error": f"cannot run bridge: {e}",
                "duration_s": time.monotonic() - t0, "raw": ""}
    duration_s = time.monotonic() - t0
    if proc.returncode != 0:
        return {"ok": False, "duration_s": duration_s, "raw": proc.stdout,
                "error": (proc.stderr or proc.stdout).strip()[-300:]}
    # parse the RESULT json block
    out = proc.stdout
    session_id = None
    if "===== RESULT =====" in out:
        blob = out.split("===== RESULT =====")[1].split("=====")[0]
        try:
            session_id = json.loads(blob).get("sessionId")
        except (ValueError, json.JSONDecodeError):
            pass
    return {"ok": True, "session_id": session_id,
            "duration_s": duration_s, "raw": out}


def run_ab(task_prompt: str, variant_a: str, variant_b: str, *,
           repo: str, tag: str = "ab",
           timeout_s: int = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Run both variants; returns session ids + per-variant results."""
    ok, bridge = judge_available()
    if not ok:
        return {"ok": False, "error": bridge}
    results = {}
    for variant, prefix in (("a", variant_a), ("b", variant_b)):
        prompt = f"{prefix}\n\n{task_prompt}" if prefix else task_prompt
        results[variant] = _bridge_prompt(
            bridge, repo, prompt, f"ab-run:{tag}:{variant}", timeout_s)
    return {"ok": all(r["ok"] for r in results.values()),
            "variants": results,
            "note": "sessions are labelled ab-run:<tag>:<variant> — "
                    "janitor can reap them; this consumed real tokens"}


# ---------------------------------------------------------------------------
# G3 harness (v2): task suites, seeded interleaving, caps, preregistered stats
# ---------------------------------------------------------------------------

G3_SCHEMA = "g3-report/0.1"
G3_DEFAULT_SEED = 73001
MIN_ATTEMPTS = 3
MIN_TRIGGER_TASKS = 5
MIN_CONTROL_TASKS = 3
TASK_KINDS = ("bugfix", "feature", "refactor")
TASK_TYPES = ("trigger", "control")

_DENIAL_RE = re.compile(r"(?i)\bdeni(?:ed|al|als)\b")


class G3Error(RuntimeError):
    """A tasks manifest, suite ratio or plan constraint is violated."""


@dataclass(frozen=True)
class G3Task:
    """One task from the manifest dir."""

    id: str
    kind: str        # bugfix | feature | refactor — selects the rubric pack
    type: str        # trigger | control
    prompt: str
    workspace: Path


@dataclass(frozen=True)
class G3Unit:
    """One scheduled attempt: task × arm × attempt index, in run order."""

    task: G3Task
    variant: str     # "a" | "b"
    attempt: int     # 0..k-1 within (task, variant)
    seq: int         # global execution order


def _task_from_entry(
    data: Any, source: str, base_dirs: list[Path]
) -> G3Task:
    """Validate one manifest entry. ``base_dirs`` are tried in order for
    relative ``workspace``/``dir`` paths."""
    if not isinstance(data, dict):
        raise G3Error(f"{source}: entry must be a JSON object")
    tid = data.get("id")
    kind = data.get("kind")
    ttype = data.get("type")
    prompt = data.get("prompt")
    ws = data.get("workspace") or data.get("dir")
    if not isinstance(tid, str) or not tid:
        raise G3Error(f'{source}: "id" must be a non-empty string')
    if kind not in TASK_KINDS:
        raise G3Error(
            f"{source}: \"kind\" must be one of {TASK_KINDS} (got {kind!r})")
    if ttype not in TASK_TYPES:
        raise G3Error(
            f"{source}: \"type\" must be one of {TASK_TYPES} (got {ttype!r})")
    if not isinstance(prompt, str) or not prompt:
        raise G3Error(f'{source}: "prompt" must be a non-empty string')
    if not isinstance(ws, str) or not ws:
        raise G3Error(
            f'{source}: "workspace" (or "dir") must be a non-empty string')
    ws_path = Path(ws)
    if not ws_path.is_absolute():
        for base in base_dirs:
            if (base / ws_path).is_dir():
                ws_path = base / ws_path
                break
        else:
            ws_path = base_dirs[0] / ws_path
    if not ws_path.is_dir():
        raise G3Error(f"{source}: workspace {ws_path} is not a directory")
    return G3Task(id=tid, kind=kind, type=ttype,
                  prompt=prompt, workspace=ws_path)


def load_tasks(tasks_dir: str | Path) -> list[G3Task]:
    """Load the task suite from a manifest dir.

    Two accepted layouts:

    - **index manifest**: ``<dir>/manifest.json`` — a JSON list (or an
      object with ``"tasks"``) of entries shaped ``{"id", "kind",
      "type", "prompt", "workspace"|"dir"}``. Relative dirs resolve
      against the manifest dir, then its parent, then the cwd — so the
      shipped ``tasks/manifest.json`` pack with ``"dir": "tasks/<id>"``
      works from the repo root.
    - **per-task manifests**: each ``*.json`` in ``<dir>`` is one task
      ``{"id", "kind", "type", "prompt", "workspace"}``; relative
      ``workspace`` paths resolve against the manifest dir.

    Every field is validated eagerly — a bad suite fails before any
    session is created.
    """
    d = Path(tasks_dir)
    if not d.is_dir():
        raise G3Error(f"{d}: no such tasks directory")
    index = d / "manifest.json"
    if index.is_file():
        try:
            data = json.loads(index.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise G3Error(f"manifest.json: invalid JSON: {exc}") from exc
        if isinstance(data, dict):
            data = data.get("tasks")
        if not isinstance(data, list):
            raise G3Error(
                "manifest.json: expected a list (or {\"tasks\": [...]})")
        base_dirs = [d, d.parent, Path.cwd()]
        return [
            _task_from_entry(e, f"manifest.json[{i}]", base_dirs)
            for i, e in enumerate(data)
        ]
    tasks: list[G3Task] = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise G3Error(f"{p.name}: invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise G3Error(f"{p.name}: top level must be a JSON object")
        if "id" not in data:
            data["id"] = p.stem
        tasks.append(_task_from_entry(data, p.name, [d]))
    return tasks


def validate_suite(
    tasks: list[G3Task],
    min_trigger: int = MIN_TRIGGER_TASKS,
    min_control: int = MIN_CONTROL_TASKS,
) -> tuple[int, int]:
    """Enforce the ≥5 trigger + ≥3 control ratio. Returns (trigger, control)."""
    trig = sum(1 for t in tasks if t.type == "trigger")
    ctrl = sum(1 for t in tasks if t.type == "control")
    if trig < min_trigger or ctrl < min_control:
        raise G3Error(
            f"suite needs ≥{min_trigger} trigger + ≥{min_control} control "
            f"tasks (got {trig} trigger, {ctrl} control)")
    return trig, ctrl


def plan_units(tasks: list[G3Task], k: int, seed: int) -> list[G3Unit]:
    """Seeded ABBA/BAAB schedule of task × arm × attempt units.

    Task order is shuffled with ``random.Random(seed)``; each task then
    runs its 2k attempts contiguously in an ``ABBA`` pattern (even task
    index) or ``BAAB`` (odd index), so the arm that goes first alternates
    across tasks and temporal drift is spread evenly between arms. Every
    task gets exactly k attempts per arm.
    """
    if k < MIN_ATTEMPTS:
        raise G3Error(f"attempts per arm must be ≥{MIN_ATTEMPTS} (got {k})")
    order = list(tasks)
    random.Random(seed).shuffle(order)
    units: list[G3Unit] = []
    seq = 0
    for i, task in enumerate(order):
        first, second = ("a", "b") if i % 2 == 0 else ("b", "a")
        counts = {"a": 0, "b": 0}
        for j in range(2 * k):
            arm = first if j % 4 in (0, 3) else second
            units.append(G3Unit(task=task, variant=arm,
                                attempt=counts[arm], seq=seq))
            counts[arm] += 1
            seq += 1
    return units


def build_design(
    tasks: list[G3Task], k: int, seed: int, caps: dict[str, Any]
) -> dict[str, Any]:
    """The preregistered design block — frozen into the report BEFORE running."""
    trig, ctrl = validate_suite(tasks)
    return {
        "tasks": [
            {"id": t.id, "kind": t.kind, "type": t.type}
            for t in sorted(tasks, key=lambda t: t.id)
        ],
        "trigger": trig,
        "control": ctrl,
        "k": k,
        "seed": seed,
        "preregistered": True,
        "caps": caps,
        "thresholds": {
            "wilson_z": g3stats.WILSON_Z,
            "bootstrap_draws": g3stats.BOOTSTRAP_DRAWS,
            "control_regression_delta": g3stats.CONTROL_REGRESSION_DELTA,
            "ci_max_width": g3stats.CI_MAX_WIDTH,
            "max_abort_rate": g3stats.MAX_ABORT_RATE,
            "baseline_ceiling": g3stats.BASELINE_CEILING,
            "baseline_floor": g3stats.BASELINE_FLOOR,
            "calibration_max_fp": g3stats.CALIBRATION_MAX_FP,
        },
    }


def _count_denials(raw: str | None) -> int | None:
    """Best-effort policy-denial count from bridge output; None if absent."""
    if not raw:
        return None
    return len(_DENIAL_RE.findall(raw))


def make_workspace_check_grader(
    python: str | None = None, timeout_s: int = 180
) -> Callable[[G3Unit, dict[str, Any]], dict[str, Any]]:
    """Grader that runs the task's own deterministic check in the attempt
    workspace copy — the mode for hermetic packs like ``tasks/``.

    Runs ``pytest tests -q`` when the copy has a ``tests/`` dir and
    ``check_structure.py`` when present (refactor layout asserts); every
    command must exit 0. A workspace with no check → grading error (the
    attempt is aborted, not failed silently).
    """
    import sys
    py = python or sys.executable

    def grade(unit: G3Unit, res: dict[str, Any]) -> dict[str, Any]:
        ws_raw = res.get("workspace")
        if not ws_raw:
            return {"success": False, "error": True,
                    "detail": "no attempt workspace recorded"}
        ws = Path(ws_raw)
        checks: list[list[str]] = []
        if (ws / "tests").is_dir():
            checks.append([py, "-m", "pytest", "tests", "-q"])
        if (ws / "check_structure.py").is_file():
            checks.append([py, "check_structure.py"])
        if not checks:
            return {"success": False, "error": True,
                    "detail": "workspace has no deterministic check"}
        ran = failed = 0
        for argv in checks:
            try:
                proc = subprocess.run(
                    argv, cwd=ws, capture_output=True, text=True,
                    timeout=timeout_s)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return {"success": False, "error": True,
                        "detail": f"workspace check failed to run: {exc}"}
            ran += 1
            failed += proc.returncode != 0
        return {"success": failed == 0, "error": False,
                "checks_run": ran, "checks_failed": failed}
    return grade


def make_bridge_runner(bridge: str) -> Callable[..., dict[str, Any]]:
    """Default ``runner`` for :func:`run_ab_v2` — one bridge session per call.

    The returned callable has signature
    ``(unit, repo_path, prompt, label, timeout_s) -> dict`` and honours the
    per-attempt timeout itself (via the bridge's ``--timeout-ms`` plus a
    subprocess ceiling).
    """
    def run(unit: G3Unit, repo: Path, prompt: str, label: str,
            timeout_s: int) -> dict[str, Any]:
        r = _bridge_prompt(bridge, str(repo), prompt, label, timeout_s)
        return {
            "ok": r["ok"],
            "session_id": r.get("session_id"),
            "error": r.get("error"),
            "duration_s": r.get("duration_s"),
            "denials": _count_denials(r.get("raw")),
        }
    return run


def environment_block() -> dict[str, Any]:
    """Best-effort environment fingerprint; every field may be null."""
    devin_version = os.environ.get("DEVIN_VERSION")
    if not devin_version:
        try:
            proc = subprocess.run(
                ["devin", "--version"], capture_output=True, text=True,
                timeout=5)
            if proc.returncode == 0 and proc.stdout.strip():
                devin_version = proc.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "devin_version": devin_version,
        "model": os.environ.get("DEVIN_MODEL"),
        "profile": "lab",
        "machine_id": os.environ.get("DEVIN_MACHINE_ID")
        or platform.node() or None,
    }


def candidate_block(
    *, aa: bool, prefix: str, candidate_file: str | Path | None = None
) -> dict[str, Any]:
    """Candidate identity. ``--candidate-file`` wins: its sha256 is pinned."""
    kind = "aa-calibration" if aa else "prompt-prefix"
    if candidate_file is not None:
        p = Path(candidate_file)
        try:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError as exc:
            raise G3Error(f"cannot read candidate file {p}: {exc}") from exc
        return {"kind": kind, "name": p.name, "sha256": digest}
    return {
        "kind": kind,
        "name": "arm-a==arm-b" if aa else "arm-b-prefix",
        "sha256": hashlib.sha256(prefix.encode("utf-8")).hexdigest(),
    }


def _calibration_ok(calibration: dict[str, Any] | None, aa: bool) -> bool | None:
    """Whether the harness is calibrated enough to claim ``improves``.

    A/A runs measure the harness itself, so they are treated as calibrated
    for verdict purposes (their verdict *is* the measurement). Otherwise a
    prior calibration block must be supplied and its false-positive rate
    must be ≤ the preregistered threshold.
    """
    if aa:
        return True
    if calibration is None:
        return None
    fp = calibration.get("aa_false_positive_rate")
    if fp is None:
        return None
    return bool(fp <= g3stats.CALIBRATION_MAX_FP)


def run_ab_v2(
    tasks: list[G3Task],
    *,
    attempts: int = 5,
    seed: int = G3_DEFAULT_SEED,
    variant_a: str = "",
    variant_b: str = "",
    aa: bool = False,
    work_dir: str | Path,
    runner: Callable[..., dict[str, Any]],
    grader: Callable[[G3Unit, dict[str, Any]], dict[str, Any]] | None = None,
    max_sessions: int | None = None,
    max_total_time: float | None = None,
    session_timeout: int = DEFAULT_TIMEOUT_S,
    calibration: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
    environment: dict[str, Any] | None = None,
    time_fn: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run the G3 A/B suite and return a ``g3-report/0.1`` report dict.

    ``runner`` does one attempt — signature ``(unit, repo_path, prompt,
    label, timeout_s) -> {"ok", "session_id", "error", "duration_s",
    "denials"}``; the bridge-backed default is :func:`make_bridge_runner`.
    ``grader`` optionally maps ``(unit, runner_result) -> {"success": bool,
    "error": bool, ...}``; without it, success = the session completed.

    Aborted (timed-out/failed/ungradable) attempts count as failures, are
    recorded separately, and are never retried. Hitting any budget cap
    aborts the whole run — what ran is still reported.
    """
    if attempts < MIN_ATTEMPTS:
        raise G3Error(
            f"attempts per arm must be ≥{MIN_ATTEMPTS} (got {attempts})")
    caps = {
        "max_sessions": max_sessions,
        "max_total_time": max_total_time,
        "session_timeout": session_timeout,
    }
    design = build_design(tasks, attempts, seed, caps)  # frozen pre-run
    units = plan_units(tasks, attempts, seed)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    t0 = time_fn()
    sessions_run = 0
    abort_reason: str | None = None
    records: list[dict[str, Any]] = []

    for unit in units:
        if max_sessions is not None and sessions_run >= max_sessions:
            abort_reason = f"max-sessions cap ({max_sessions}) reached"
            break
        if max_total_time is not None and time_fn() - t0 >= max_total_time:
            abort_reason = (
                f"max-total-time cap ({max_total_time}s) reached")
            break

        rec: dict[str, Any] = {
            "task": unit.task.id, "variant": unit.variant,
            "attempt": unit.attempt, "session_id": None,
            "success": False, "aborted": False,
            "duration_s": None, "tool_calls": None,
            "denials": None, "error": None,
        }
        dest = work_dir / unit.task.id / unit.variant / f"attempt-{unit.attempt}"
        try:
            # never ship reference solutions or caches into an attempt
            shutil.copytree(
                unit.task.workspace, dest,
                ignore=shutil.ignore_patterns("_solution", "__pycache__"))
        except OSError as exc:
            rec["aborted"] = True
            rec["error"] = f"workspace copy failed: {exc}"
            records.append(rec)
            continue

        prefix = variant_a if (aa or unit.variant == "a") else variant_b
        prompt = f"{prefix}\n\n{unit.task.prompt}" if prefix else unit.task.prompt
        label = f"g3-ab:{unit.task.id}:{unit.variant}:{unit.attempt}"
        try:
            res = runner(unit, dest, prompt, label, session_timeout)
        except Exception as exc:  # a crashing runner must not kill the suite
            res = {"ok": False, "error": f"runner raised: {exc}"}
        if not isinstance(res, dict):
            res = {"ok": False, "error": "runner returned a non-dict result"}
        res.setdefault("workspace", str(dest))
        sessions_run += 1
        rec.update({
            "session_id": res.get("session_id"),
            "duration_s": res.get("duration_s"),
            "denials": res.get("denials"),
            "error": res.get("error"),
        })
        if not res.get("ok"):
            rec["aborted"] = True
        else:
            try:
                g = grader(unit, res) if grader is not None else {"success": True}
            except Exception as exc:
                g = {"success": False, "error": True}
                rec["error"] = f"grader raised: {exc}"
            if g.get("error"):
                rec["aborted"] = True
                if rec["error"] is None:
                    rec["error"] = "grading failed"
            else:
                rec["success"] = bool(g.get("success"))
            if g.get("tool_calls") is not None:
                rec["tool_calls"] = g["tool_calls"]
            if g.get("duration_s") is not None:
                rec["duration_s"] = g["duration_s"]
            if g.get("denials") is not None:
                rec["denials"] = g["denials"]
        records.append(rec)

    # ---- aggregates -------------------------------------------------------
    trig_ids = {t.id for t in tasks if t.type == "trigger"}

    def _arm_block(variant: str) -> dict[str, Any]:
        trig_recs = [r for r in records
                     if r["variant"] == variant and r["task"] in trig_ids]
        att = len(trig_recs)
        succ = sum(1 for r in trig_recs if r["success"])
        aborted = sum(1 for r in trig_recs if r["aborted"])
        all_recs = [r for r in records if r["variant"] == variant]
        lo, hi = g3stats.wilson_interval(succ, att)
        return {
            "successes": succ,
            "attempted": att,
            "aborted": aborted,
            "success_rate": (succ / att) if att else None,
            "ci95": [round(lo, 6), round(hi, 6)],
            "sessions": [r["session_id"] for r in all_recs
                         if r["session_id"]],
        }

    def _task_rate(variant: str, task_id: str) -> tuple[int, int]:
        recs = [r for r in records
                if r["variant"] == variant and r["task"] == task_id]
        return (sum(1 for r in recs if r["success"]), len(recs))

    deltas: dict[str, float] = {}
    for t in tasks:
        if t.type != "trigger":
            continue
        sa, na = _task_rate("a", t.id)
        sb, nb = _task_rate("b", t.id)
        if na and nb:
            deltas[t.id] = sb / nb - sa / na
    control_deltas: dict[str, float] = {}
    for t in tasks:
        if t.type != "control":
            continue
        sa, na = _task_rate("a", t.id)
        sb, nb = _task_rate("b", t.id)
        if na and nb:
            control_deltas[t.id] = sb / nb - sa / na

    delta_mean = g3stats.mean(deltas.values())
    delta_ci = g3stats.bootstrap_ci(list(deltas.values()), seed)

    def _denials(variant: str) -> int | None:
        vals = [r["denials"] for r in records
                if r["variant"] == variant and r["denials"] is not None]
        return sum(vals) if vals else None

    def _abort_rate(variant: str) -> float:
        recs = [r for r in records if r["variant"] == variant]
        if not recs:
            return 0.0
        return sum(1 for r in recs if r["aborted"]) / len(recs)

    denials_a, denials_b = _denials("a"), _denials("b")
    baseline_rate = None
    sa_all = [r for r in records if r["variant"] == "a" and r["task"] in trig_ids]
    if sa_all:
        baseline_rate = sum(1 for r in sa_all if r["success"]) / len(sa_all)

    v = g3stats.verdict(
        delta_ci=delta_ci,
        control_deltas=control_deltas.values(),
        denials_a=denials_a, denials_b=denials_b,
        baseline_rate=baseline_rate,
        abort_rate_a=_abort_rate("a"), abort_rate_b=_abort_rate("b"),
        calibration_ok=_calibration_ok(calibration, aa),
    )

    calibration_block: dict[str, Any] | None
    if aa:
        calibration_block = {
            "aa_runs": 1,
            "aa_false_positive_rate":
                1.0 if v in ("regresses", "improves") else 0.0,
        }
    elif calibration is not None:
        calibration_block = dict(calibration)
    else:
        calibration_block = None

    notes = [
        "sessions labelled g3-ab:<task>:<variant>:<attempt> — janitor can "
        "reap them; this consumed real tokens",
        f"work_dir={work_dir}",
    ]
    if aa:
        notes.append("A/A calibration run — arm B used the arm A prefix")
    if abort_reason:
        notes.append(f"run aborted early: {abort_reason}; "
                     "partial results reported, no retries")

    return {
        "schema": G3_SCHEMA,
        "candidate": candidate
        or candidate_block(aa=aa, prefix=variant_b if not aa else variant_a),
        "environment": environment or environment_block(),
        "design": design,
        "calibration": calibration_block,
        "results": {
            "arm_a": _arm_block("a"),
            "arm_b": _arm_block("b"),
            "delta_trigger": {
                "mean": delta_mean,
                "ci95": list(delta_ci) if delta_ci else None,
            },
            "control_regressions": [
                {"task": tid, "delta": d}
                for tid, d in sorted(control_deltas.items())
                if d <= g3stats.CONTROL_REGRESSION_DELTA
            ],
            "safety": {"denials_a": denials_a, "denials_b": denials_b},
            "attempts": records,
        },
        "verdict": v,
        "notes": " | ".join(notes),
    }

