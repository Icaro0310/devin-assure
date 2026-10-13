"""Composite action contract: pins the gate semantics fixed in review.

- the action installs the harness from `github.action_path` (the
  revision under test), not the latest PyPI release;
- inputs reach bash only through env vars, never `${{ inputs.* }}`
  inside `run:` blocks;
- `devin-evals run` fails when every case skips (nothing graded).
"""

from __future__ import annotations

import re
from pathlib import Path

ACTION = Path(__file__).parents[1] / "action.yml"


def test_action_installs_action_source():
    runs = ACTION.read_text(encoding="utf-8")
    assert "pip install devin-evals" not in runs
    assert "github.action_path" in runs


def test_action_inputs_not_interpolated_in_run_blocks():
    runs = ACTION.read_text(encoding="utf-8")
    for block in re.findall(r"run: *\|?\n((?: +.*\n?)+)", runs):
        assert "${{ inputs." not in block


def test_run_fails_when_every_case_skips(capsys, tmp_path, monkeypatch):
    from devin_evals import cli

    report = {"cases": [],
              "summary": {"total": 3, "passed": 0, "failed": 0,
                          "skipped": 3, "errored": 0}}
    monkeypatch.setattr(cli, "run_evals", lambda *a, **k: report)

    class A:
        evals = tmp_path
        sessions_db = tmp_path / "s.db"
        out = str(tmp_path / "out")
        packs_dir = ""

    assert cli._cmd_run(A()) == 1
    assert "skipped" in capsys.readouterr().err
