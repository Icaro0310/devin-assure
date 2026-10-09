"""EV-5: A/B run — fail-closed, labelled, dry-run safe (v1 + G3 v2 CLI)."""
import json
from pathlib import Path

import pytest

from devin_evals.cli import main


def test_abrun_dry_run_needs_no_bridge(tmp_path, capsys):
    assert main(["ab-run", "--task", "t", "--repo", str(tmp_path),
                 "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["would_create"] == ["ab-run:ab:a", "ab-run:ab:b"]


def test_abrun_fail_closed_without_bridge(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DEVIN_BRIDGE_CMD", raising=False)
    rc = main(["ab-run", "--task", "t", "--repo", str(tmp_path)])
    assert rc == 2
    assert "DEVIN_BRIDGE_CMD" in capsys.readouterr().err


def test_abrun_v1_task_needs_repo(tmp_path, capsys):
    rc = main(["ab-run", "--task", "t", "--dry-run"])
    assert rc == 2
    assert "--repo" in capsys.readouterr().err


# --- G3 v2 CLI -------------------------------------------------------------

def _suite(evals_dir: Path, tmp_path: Path, n_trig=5, n_ctrl=3) -> Path:
    """evals dir with one pack-backed case + tasks/ manifests."""
    tasks_dir = evals_dir / "tasks"
    tasks_dir.mkdir()
    ws_root = tmp_path / "ws"
    for i in range(n_trig):
        _task(tasks_dir, ws_root, f"trig-{i}", "trigger")
    for i in range(n_ctrl):
        _task(tasks_dir, ws_root, f"ctrl-{i}", "control")
    return tasks_dir


def _task(tasks_dir: Path, ws_root: Path, tid: str, ttype: str):
    ws = ws_root / tid
    ws.mkdir(parents=True, exist_ok=True)
    (tasks_dir / f"{tid}.json").write_text(json.dumps(
        {"id": tid, "kind": "bugfix", "type": ttype,
         "prompt": f"do {tid}", "workspace": str(ws)}))


def test_v2_dry_run_prints_plan(tmp_path, capsys):
    evals = tmp_path / "evals"
    evals.mkdir()
    _suite(evals, tmp_path)
    rc = main(["ab-run", "--evals", str(evals), "--attempts", "3",
               "--dry-run"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    plan = out["plan"]
    assert plan["total_sessions"] == 48
    assert plan["trigger"] == 5 and plan["control"] == 3
    assert len(plan["order"]) == 48


def test_v2_evals_required(tmp_path, capsys):
    rc = main(["ab-run", "--tasks", str(tmp_path), "--dry-run"])
    assert rc == 2
    assert "--evals" in capsys.readouterr().err


def test_v2_ratio_enforced(tmp_path, capsys):
    evals = tmp_path / "evals"
    evals.mkdir()
    _suite(evals, tmp_path, n_trig=4)
    rc = main(["ab-run", "--evals", str(evals), "--attempts", "3",
               "--dry-run"])
    assert rc == 2
    assert "trigger" in capsys.readouterr().err


def test_v2_over_cap_requires_confirm(tmp_path, capsys):
    evals = tmp_path / "evals"
    evals.mkdir()
    _suite(evals, tmp_path)
    rc = main(["ab-run", "--evals", str(evals), "--attempts", "3",
               "--max-sessions", "10"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--confirm" in err and "--yes" in err


def test_v2_confirm_declined(tmp_path, capsys, monkeypatch):
    evals = tmp_path / "evals"
    evals.mkdir()
    _suite(evals, tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    rc = main(["ab-run", "--evals", str(evals), "--attempts", "3",
               "--max-sessions", "10", "--confirm"])
    assert rc == 2
    assert "aborted" in capsys.readouterr().out


def test_v2_yes_proceeds_to_bridge_check(tmp_path, capsys, monkeypatch):
    evals = tmp_path / "evals"
    evals.mkdir()
    _suite(evals, tmp_path)
    monkeypatch.delenv("DEVIN_BRIDGE_CMD", raising=False)
    rc = main(["ab-run", "--evals", str(evals), "--attempts", "3",
               "--max-sessions", "10", "--yes"])
    assert rc == 2
    assert "DEVIN_BRIDGE_CMD" in capsys.readouterr().err


def test_v2_fail_closed_without_bridge(tmp_path, capsys, monkeypatch):
    evals = tmp_path / "evals"
    evals.mkdir()
    _suite(evals, tmp_path)
    monkeypatch.delenv("DEVIN_BRIDGE_CMD", raising=False)
    rc = main(["ab-run", "--evals", str(evals), "--attempts", "3"])
    assert rc == 2
    assert "DEVIN_BRIDGE_CMD" in capsys.readouterr().err


def test_v2_full_run_with_fake_runner(tmp_path, capsys, monkeypatch):
    """End-to-end: fake bridge env + injected runner → report on disk."""
    import devin_evals.abrun as abrun_mod

    evals = tmp_path / "evals"
    evals.mkdir()
    _suite(evals, tmp_path)
    monkeypatch.setenv("DEVIN_BRIDGE_CMD", "fake-bridge.js")

    def fake_factory(bridge):
        def run(unit, repo, prompt, label, timeout_s):
            return {"ok": True,
                    "session_id": f"s-{unit.variant}-{unit.attempt}",
                    "duration_s": 1.0, "denials": 0}
        return run
    monkeypatch.setattr(abrun_mod, "make_bridge_runner", fake_factory)

    out = tmp_path / "report.json"
    rc = main(["ab-run", "--evals", str(evals), "--attempts", "3",
               "--seed", "5", "--work-dir", str(tmp_path / "w"),
               "--out", str(out)])
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["schema"] == "g3-report/0.1"
    assert report["design"]["preregistered"] is True
    assert len(report["results"]["attempts"]) == 48
    stdout = capsys.readouterr().out
    assert "verdict:" in stdout


def test_v2_attempts_below_min(tmp_path, capsys):
    evals = tmp_path / "evals"
    evals.mkdir()
    _suite(evals, tmp_path)
    rc = main(["ab-run", "--evals", str(evals), "--attempts", "2",
               "--dry-run"])
    assert rc == 2
    assert "3" in capsys.readouterr().err
