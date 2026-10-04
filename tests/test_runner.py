"""Offline replay: sessions.db → evidence → graded report.json / report.md."""

from __future__ import annotations

import json

from devin_evals.runner import run_evals

from conftest import SESSION_ID, SESSION_TITLE, write_eval

PASS_CASE = {
    "id": "ok",
    "description": "passes on the fixture session",
    "session_ref": SESSION_TITLE,
    "rubric": [
        {"grader": "contains", "text": "all tests pass"},
        {"grader": "tool_called", "name": "run_shell", "args_substr": "pytest"},
        {"grader": "exit_code", "value": 0, "mode": "all"},
        {"grader": "no_secrets"},
    ],
}


def test_report_shape(evals_dir, sessions_db):
    write_eval(evals_dir, "a", PASS_CASE)
    report = run_evals(evals_dir, sessions_db)
    assert report["tool"] == "devin-evals"
    assert report["version"]
    assert report["schema_version"] == 17
    assert report["sessions_db"].endswith("sessions.db")
    (case,) = report["cases"]
    assert case["id"] == "ok"
    assert case["status"] == "pass"
    assert len(case["checks"]) == 4
    assert all(c["passed"] for c in case["checks"])
    assert case["checks"][0]["grader"] == "contains"
    summary = report["summary"]
    assert summary == {
        "total": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "errored": 0,
        "score": 1.0,
    }


def test_failing_check_marks_case_failed(evals_dir, sessions_db):
    write_eval(
        evals_dir,
        "a",
        {**PASS_CASE, "rubric": [{"grader": "contains", "text": "never said"}]},
    )
    report = run_evals(evals_dir, sessions_db)
    case = report["cases"][0]
    assert case["status"] == "fail"
    assert report["summary"]["failed"] == 1
    assert report["summary"]["score"] == 0.0


def test_session_ref_matches_id_and_title(evals_dir, sessions_db):
    write_eval(evals_dir, "by-id", {**PASS_CASE, "id": "by-id", "session_ref": SESSION_ID})
    write_eval(
        evals_dir, "by-title", {**PASS_CASE, "id": "by-title", "session_ref": SESSION_TITLE}
    )
    report = run_evals(evals_dir, sessions_db)
    assert all(c["status"] == "pass" for c in report["cases"])


def test_missing_session_ref_skips(evals_dir, sessions_db):
    write_eval(
        evals_dir,
        "a",
        {**PASS_CASE, "session_ref": None, "prompt_context": "live-only case"},
    )
    report = run_evals(evals_dir, sessions_db)
    case = report["cases"][0]
    assert case["status"] == "skip"
    assert case["checks"] == []
    assert report["summary"]["skipped"] == 1


def test_unknown_session_ref_errors(evals_dir, sessions_db):
    write_eval(evals_dir, "a", {**PASS_CASE, "session_ref": "ghost session"})
    report = run_evals(evals_dir, sessions_db)
    case = report["cases"][0]
    assert case["status"] == "error"
    assert "not found" in case["detail"]
    assert report["summary"]["errored"] == 1


def test_per_check_error_marks_case_error(evals_dir, sessions_db):
    write_eval(
        evals_dir,
        "a",
        {**PASS_CASE, "rubric": [{"grader": "exit_code", "mode": "bogus"}]},
    )
    report = run_evals(evals_dir, sessions_db)
    case = report["cases"][0]
    assert case["status"] == "error"


def test_writes_report_files(evals_dir, sessions_db, tmp_path):
    write_eval(evals_dir, "a", PASS_CASE)
    out = tmp_path / "report"
    run_evals(evals_dir, sessions_db, out_dir=out)
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert data["cases"][0]["id"] == "ok"
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "ok" in md and "PASS" in md and "100%" in md


def test_deterministic_rerun(evals_dir, sessions_db, tmp_path):
    write_eval(evals_dir, "a", PASS_CASE)
    write_eval(
        evals_dir,
        "b",
        {**PASS_CASE, "id": "b", "rubric": [{"grader": "contains", "text": "nope"}]},
    )
    out1, out2 = tmp_path / "r1", tmp_path / "r2"
    run_evals(evals_dir, sessions_db, out_dir=out1)
    run_evals(evals_dir, sessions_db, out_dir=out2)
    assert (out1 / "report.json").read_bytes() == (out2 / "report.json").read_bytes()
    assert (out1 / "report.md").read_bytes() == (out2 / "report.md").read_bytes()


def test_aggregate_score(evals_dir, sessions_db):
    write_eval(evals_dir, "a", PASS_CASE)
    write_eval(
        evals_dir,
        "b",
        {**PASS_CASE, "id": "b", "rubric": [{"grader": "contains", "text": "nope"}]},
    )
    report = run_evals(evals_dir, sessions_db)
    assert report["summary"]["score"] == 0.5


def test_case_score_is_fraction_of_checks(evals_dir, sessions_db):
    write_eval(
        evals_dir,
        "a",
        {
            **PASS_CASE,
            "rubric": [
                {"grader": "contains", "text": "all tests pass"},
                {"grader": "contains", "text": "nope"},
            ],
        },
    )
    report = run_evals(evals_dir, sessions_db)
    assert report["cases"][0]["score"] == 0.5


# -- EV-2: session_ref selectors ---------------------------------------------

def test_session_ref_latest_and_project_and_window(tmp_path):
    from devin_internals.fixtures import create_sessions_db
    from devin_internals.parsers.sessions import SessionsStore
    from devin_evals.runner import _find_session
    import sqlite3, json

    db = create_sessions_db(tmp_path / "sessions.db")
    con = sqlite3.connect(db)
    ids = con.execute("SELECT id FROM sessions ORDER BY created_at").fetchall()
    con.execute("UPDATE sessions SET working_directory = ? WHERE id = ?",
                ("/work/proj-alpha", ids[0][0]))
    con.execute("UPDATE sessions SET created_at = ? WHERE id = ?",
                (1_780_000_000_000, ids[0][0]))
    con.commit(); con.close()

    with SessionsStore(db) as store:
        latest = _find_session(store, "latest")
        assert latest is not None and latest.id != ids[0][0] or len(ids) < 2
        proj = _find_session(store, "project:proj-alpha")
        assert proj is not None and proj.id == ids[0][0]
        win = _find_session(store, "window:2020-01-01:2030-01-01")
        assert win is not None
        none1 = _find_session(store, "project:no-such-dir-zzz")
        assert none1 is None
        bad = _find_session(store, "window:not-a-date:x")
        assert bad is None
