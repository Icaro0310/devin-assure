"""verify.py — cross-checking claims against tool_call_state + git log."""

import json

from devin_internals.parsers.sessions import ToolCallState
from devin_qa_pack.claims import COMMIT, FILE, HTTP, PUSH, TESTS, Claim
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


def test_terminal_exit_code_overrides_completed_status():
    # Real exec updates record status="completed" even when the command
    # exits non-zero — the exit code lives in _meta.terminal_exit.
    state = tstate(
        "tc",
        {"kind": "execute", "title": "Ran command",
         "rawInput": {"command": "pytest"}},
        {"toolCallId": "tc", "status": "completed",
         "_meta": {"terminal_exit": {"exit_code": 1, "signal": None}}},
    )
    parsed = parse_tool_call(state)
    assert parsed is not None
    assert parsed.status == "failed"


def test_terminal_exit_disputes_passing_tests_claim():
    update = {"toolCallId": "tc", "status": "completed",
              "_meta": {"terminal_exit": {"exit_code": 1, "signal": None}}}
    calls = parse_tool_calls([
        tstate("tc", {"kind": "execute", "title": "Ran command",
                      "rawInput": {"command": "pytest"}}, update)
    ])
    result = verify_claim(claim(TESTS, TESTS), calls, "/nonexistent")
    assert result.status == DISPUTED


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
    parse_tool_calls([tstate("tc-null")])  # NULL payloads → dropped
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


# -- http claims ---------------------------------------------------------------


def test_http_claim_verified_by_matching_status_in_output():
    calls = parse_tool_calls(
        [exec_state("curl -i https://api.example.test/health",
                    output="HTTP/1.1 200 OK\n\nok")]
    )
    result = verify_claim(claim(HTTP, "200"), calls, "/nonexistent")
    assert result.status == VERIFIED


def test_http_claim_verified_by_status_code_field():
    calls = parse_tool_calls(
        [tstate("tc", {"kind": "execute",
                       "rawOutput": {"status_code": 200, "body": "ok"}})]
    )
    result = verify_claim(claim(HTTP, "200"), calls, "/nonexistent")
    assert result.status == VERIFIED


def test_http_claim_disputed_by_different_status():
    calls = parse_tool_calls(
        [exec_state("curl -i https://api.example.test/health",
                    output="HTTP/1.1 500 Internal Server Error")]
    )
    result = verify_claim(claim(HTTP, "200"), calls, "/nonexistent")
    assert result.status == DISPUTED
    assert "500" in result.evidence


def test_http_claim_unverifiable_when_no_status_recorded():
    calls = parse_tool_calls([exec_state("git status")])
    result = verify_claim(claim(HTTP, "200"), calls, "/nonexistent")
    assert result.status == UNVERIFIABLE


def test_http_claim_unverifiable_when_ground_truth_unreadable():
    states = [tstate("tc-null")]  # NULL payloads → dropped
    result = verify_claim(claim(HTTP, "200"), parse_tool_calls(states),
                          "/nonexistent", raw_call_count=len(states))
    assert result.status == UNVERIFIABLE


def test_http_claim_ignores_call_state_not_http():
    # the ACP call status ("completed") must not read as an HTTP code
    calls = parse_tool_calls([exec_state("pytest -q")])
    result = verify_claim(claim(HTTP, "200"), calls, "/nonexistent")
    assert result.status == UNVERIFIABLE


# -- QA-2: URL claims + --online opt-in --------------------------------------

def _url_claim(url: str) -> Claim:
    return Claim(kind="url", detail=url, session_id="s", node_id=1,
                 excerpt=f"deployed to {url}")


def test_url_claim_offline_is_unverifiable():
    from devin_qa_pack.verify import _verify_url
    r = _verify_url(_url_claim("https://example.com/x"), False, ("example.com",))
    assert r.status == UNVERIFIABLE
    assert "--online" in r.evidence


def test_url_claim_not_allowlisted():
    from devin_qa_pack.verify import _verify_url
    r = _verify_url(_url_claim("https://evil.example/x"), True, ("good.com",))
    assert r.status == UNVERIFIABLE
    assert "not in --allow-domain" in r.evidence


def test_url_allowlist_subdomain():
    from devin_qa_pack.verify import _url_allowed
    assert _url_allowed("https://app.example.com/x", ("example.com",))
    assert not _url_allowed("https://notexample.com/x", ("example.com",))


def test_url_claim_extracted_from_deploy_text():
    from devin_internals.parsers.sessions import MessageNode
    from devin_qa_pack.claims import extract_claims
    node = MessageNode(
        row_id=1, session_id="s", node_id=1, parent_node_id=None,
        chat_message='{"role":"assistant","content":"Deployed to https://demo.example.com/app — it is live."}',
        created_at=0, metadata=None,
    )
    claims = extract_claims([node])
    urls = [c.detail for c in claims if c.kind == "url"]
    assert urls == ["https://demo.example.com/app"]
