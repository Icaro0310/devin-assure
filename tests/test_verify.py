"""verify.py — cross-checking claims against tool_call_state + git log."""

import json
from pathlib import Path

from devin_internals.parsers.sessions import ToolCallState

from devin_qa_pack.claims import COMMIT, FILE, PUSH, TESTS, Claim
from devin_qa_pack.verify import (
    DISPUTED,
    UNVERIFIABLE,
    VERIFIED,
    parse_tool_call,
    parse_tool_calls,
    verify_claim,
)


def claim(kind, detail=""):
    return Claim(kind=kind, detail=detail, session_id="s", node_id=1, excerpt="x")


def tstate(tcid, call=None, update=None):
    return ToolCallState(
        session_id="s",
        tool_call_id=tcid,
        tool_call_json=json.dumps(call) if call is not None else None,
        tool_call_update_json=json.dumps(update) if update is not None else None,
    )


def exec_state(command, status="completed", output=""):
    return tstate(
        "tc",
        {
            "kind": "execute",
            "title": command,
            "status": status,
            "rawInput": {"command": command},
            "rawOutput": {"output": output},
        },
    )


def test_parse_tool_call_extracts_status_and_command():
    parsed = parse_tool_call(exec_state("python -m pytest -q"))
    assert parsed is not None
    assert parsed.status == "completed"
    assert "python -m pytest -q" in parsed.commands


def test_update_json_overrides_call_status():
    state = tstate(
        "tc",
        {"kind": "execute", "status": "in_progress",
         "rawInput": {"command": "pytest"}},
        {"status": "failed"},
    )
    parsed = parse_tool_call(state)
    assert parsed is not None
    assert parsed.status == "failed"


def test_null_payloads_parse_to_none():
    assert parse_tool_call(tstate("tc-null")) is None


def test_unparseable_payloads_parse_to_none():
    assert parse_tool_call(tstate("tc", "{not json", "{also not")) is None


# -- tests claims ------------------------------------------------------------


def test_tests_claim_verified_by_successful_runner_call():
    calls = parse_tool_calls([exec_state("python -m pytest -q")])
    result = verify_claim(claim(TESTS, "pytest"), calls, "/nonexistent")
    assert result.status == VERIFIED


def test_tests_claim_disputed_by_failed_runner_call():
    calls = parse_tool_calls([exec_state("python -m pytest -q", status="failed")])
    result = verify_claim(claim(TESTS, "pytest"), calls, "/nonexistent")
    assert result.status == DISPUTED


def test_tests_claim_disputed_when_no_matching_call():
    calls = parse_tool_calls([exec_state("git status")])
    result = verify_claim(claim(TESTS, "pytest"), calls, "/nonexistent")
    assert result.status == DISPUTED


def test_tests_claim_disputed_when_no_calls_at_all():
    result = verify_claim(claim(TESTS, "tests"), [], "/nonexistent")
    assert result.status == DISPUTED


def test_tests_claim_unverifiable_when_ground_truth_unreadable():
    calls = parse_tool_calls([tstate("tc-null")])  # NULL payloads → dropped
    # an unparseable row means ground truth exists but can't be read
    states = [tstate("tc-null")]
    result = verify_claim(claim(TESTS, "tests"), parse_tool_calls(states),
                          "/nonexistent", raw_call_count=len(states))
    assert result.status == UNVERIFIABLE


# -- push claims -------------------------------------------------------------


def test_push_claim_verified_by_git_push_call():
    calls = parse_tool_calls([exec_state("git push origin main")])
    assert verify_claim(claim(PUSH), calls, "/nonexistent").status == VERIFIED


def test_push_claim_disputed_without_push_call():
    calls = parse_tool_calls([exec_state("git commit -m x")])
    assert verify_claim(claim(PUSH), calls, "/nonexistent").status == DISPUTED


# -- commit claims -----------------------------------------------------------


def test_commit_claim_verified_when_hash_in_call_output():
    calls = parse_tool_calls(
        [exec_state("git commit -m fix", output="[main a1b2c3d] fix")]
    )
    result = verify_claim(claim(COMMIT, "a1b2c3d"), calls, "/nonexistent")
    assert result.status == VERIFIED


def test_commit_claim_verified_against_git_log(git_repo):
    repo, sha = git_repo
    result = verify_claim(claim(COMMIT, sha[:7]), [], str(repo))
    assert result.status == VERIFIED


def test_commit_claim_disputed_when_repo_lacks_hash(git_repo):
    repo, _sha = git_repo
    result = verify_claim(claim(COMMIT, "badbadc"), [], str(repo))
    assert result.status == DISPUTED


def test_commit_claim_disputed_without_any_evidence():
    result = verify_claim(claim(COMMIT, "a1b2c3d"), [], "/nonexistent")
    assert result.status == DISPUTED


# -- file claims -------------------------------------------------------------


def test_file_claim_verified_by_write_call_referencing_path():
    calls = parse_tool_calls(
        [tstate("tc", {"kind": "edit", "title": "src/report.html",
                       "status": "completed",
                       "rawInput": {"path": "src/report.html"}})]
    )
    result = verify_claim(claim(FILE, "src/report.html"), calls, "/nonexistent")
    assert result.status == VERIFIED


def test_file_claim_verified_when_file_exists_on_disk(tmp_path):
    (tmp_path / "out.txt").write_text("x", encoding="utf-8")
    result = verify_claim(claim(FILE, "out.txt"), [], str(tmp_path))
    assert result.status == VERIFIED


def test_file_claim_disputed_when_file_missing_on_disk(tmp_path):
    result = verify_claim(claim(FILE, "missing.txt"), [], str(tmp_path))
    assert result.status == DISPUTED


def test_file_claim_unverifiable_without_disk_or_calls():
    result = verify_claim(claim(FILE, "docs/spec.pdf"), [], "/nonexistent")
    assert result.status == UNVERIFIABLE
