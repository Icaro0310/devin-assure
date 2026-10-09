"""CLI: `devin-evals list` and `devin-evals run`."""

from __future__ import annotations

import json

from conftest import SESSION_TITLE, write_eval
from devin_evals.cli import main

PASS_CASE = {
    "id": "cli-ok",
    "description": "cli smoke",
    "session_ref": SESSION_TITLE,
    "rubric": [{"grader": "contains", "text": "all tests pass"}],
}
FAIL_CASE = {
    "id": "cli-fail",
    "description": "cli fail",
    "session_ref": SESSION_TITLE,
    "rubric": [{"grader": "contains", "text": "never produced"}],
}


def test_list_prints_cases(evals_dir, capsys):
    write_eval(evals_dir, "a", PASS_CASE)
    assert main(["list", "--evals", str(evals_dir)]) == 0
    out = capsys.readouterr().out
    assert "cli-ok" in out and "cli smoke" in out and "1 check" in out


def test_list_empty(evals_dir, capsys):
    assert main(["list", "--evals", str(evals_dir)]) == 0
    assert "no eval cases" in capsys.readouterr().out


def test_list_bad_dir_returns_2(evals_dir, capsys):
    assert main(["list", "--evals", str(evals_dir / "nope")]) == 2
    assert "no such" in capsys.readouterr().err


def test_run_writes_reports_and_returns_0(evals_dir, sessions_db, tmp_path, capsys):
    write_eval(evals_dir, "a", PASS_CASE)
    out = tmp_path / "out"
    rc = main(
        ["run", "--evals", str(evals_dir), "--sessions-db", str(sessions_db), "--out", str(out)]
    )
    assert rc == 0
    assert (out / "report.json").exists() and (out / "report.md").exists()
    stdout = capsys.readouterr().out
    assert "cli-ok" in stdout and "1/1" in stdout


def test_run_returns_1_on_failure(evals_dir, sessions_db, tmp_path):
    write_eval(evals_dir, "a", FAIL_CASE)
    rc = main(
        ["run", "--evals", str(evals_dir), "--sessions-db", str(sessions_db), "--out", str(tmp_path / "o")]
    )
    assert rc == 1


def test_run_missing_db_returns_2(evals_dir, tmp_path, capsys):
    write_eval(evals_dir, "a", PASS_CASE)
    rc = main(
        ["run", "--evals", str(evals_dir), "--sessions-db", str(tmp_path / "x.db")]
    )
    assert rc == 2
    assert capsys.readouterr().err


def test_run_default_out_dir(evals_dir, sessions_db, tmp_path, monkeypatch):
    write_eval(evals_dir, "a", PASS_CASE)
    monkeypatch.chdir(tmp_path)
    rc = main(["run", "--evals", str(evals_dir), "--sessions-db", str(sessions_db)])
    assert rc == 0
    data = json.loads((tmp_path / "eval-report" / "report.json").read_text())
    assert data["cases"][0]["id"] == "cli-ok"
