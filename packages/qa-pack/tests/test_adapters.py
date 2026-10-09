"""Adapter tests — synthetic transcripts only, never real agent data."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from devin_qa_pack.adapters import aider, claude_code
from devin_qa_pack.cli import main
from devin_qa_pack.report import PARTIAL, PASS, UNVERIFIED, audit_source
from devin_qa_pack.verify import UNVERIFIABLE, VERIFIED


AIDER_HISTORY = """\
# aider chat started at 2026-01-01 10:00:00

> /add src/app.py

#### fix the flaky test and run the suite

I updated src/app.py to handle the retry and committed {sha}.

> /run python -m pytest -q

All tests passed — pytest is green.
"""


def test_aider_commit_verified_via_git_log(git_repo, tmp_path: Path) -> None:
    repo, sha = git_repo
    hist = tmp_path / ".aider.chat.history.md"
    hist.write_text(AIDER_HISTORY.format(sha=sha), encoding="utf-8")

    audit = audit_source(aider.load(hist, working_directory=str(repo)))

    assert audit.verdict == PARTIAL  # verified commit + unverifiable tests
    by_kind = {r.claim.kind: r.status for r in audit.results}
    assert by_kind["commit"] == VERIFIED
    assert by_kind["tests"] == UNVERIFIABLE


def test_aider_missing_commit_is_disputed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    hist = tmp_path / ".aider.chat.history.md"
    hist.write_text(AIDER_HISTORY.format(sha="deadbee"), encoding="utf-8")

    audit = audit_source(aider.load(hist, working_directory=str(repo)))

    by_kind = {r.claim.kind: r.status for r in audit.results}
    assert by_kind["commit"] != VERIFIED


def test_aider_slash_commands_do_not_fabricate_pass(tmp_path: Path) -> None:
    """A recorded /run without a tests claim still audits UNVERIFIED."""
    hist = tmp_path / ".aider.chat.history.md"
    hist.write_text(
        "# aider chat started\n\n> /run pytest -q\n", encoding="utf-8"
    )
    assert audit_source(aider.load(hist)).verdict == UNVERIFIED


CLAUDE_TRANSCRIPT = [
    {
        "type": "user",
        "cwd": "/tmp/proj",
        "message": {"role": "user", "content": "create src/new.py and test it"},
    },
    {
        "type": "assistant",
        "cwd": "/tmp/proj",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu1", "name": "Write",
                 "input": {"file_path": "src/new.py"}},
                {"type": "tool_use", "id": "tu2", "name": "Bash",
                 "input": {"command": "python -m pytest -q"}},
            ],
        },
    },
    {
        "type": "user",
        "cwd": "/tmp/proj",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": "ok"},
                {"type": "tool_result", "tool_use_id": "tu2",
                 "content": "12 passed", "is_error": False},
            ],
        },
    },
    {
        "type": "assistant",
        "cwd": "/tmp/proj",
        "message": {
            "role": "assistant",
            "content": [{"type": "text",
                         "text": "I created src/new.py and the tests passed "
                                 "under pytest."}],
        },
    },
]


def _write_jsonl(path: Path, entries: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
    )
    return path


def test_claude_code_transcript_verifies(tmp_path: Path) -> None:
    f = _write_jsonl(tmp_path / "session.jsonl", CLAUDE_TRANSCRIPT)
    view = claude_code.load(f)

    assert view.working_directory == "/tmp/proj"  # from the cwd field
    audit = audit_source(view)

    assert audit.verdict == PASS
    statuses = {r.claim.kind: r.status for r in audit.results}
    assert statuses["file"] == VERIFIED
    assert statuses["tests"] == VERIFIED


def test_claude_code_failed_result_disputes(tmp_path: Path) -> None:
    entries = list(CLAUDE_TRANSCRIPT)
    entries[2] = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": "ok"},
                {"type": "tool_result", "tool_use_id": "tu2",
                 "content": "3 failed", "is_error": True},
            ],
        },
    }
    f = _write_jsonl(tmp_path / "session.jsonl", entries)

    audit = audit_source(claude_code.load(f))

    assert audit.verdict == PARTIAL
    tests = next(r for r in audit.results if r.claim.kind == "tests")
    assert tests.status == "disputed"


def test_cli_transcript_mode(tmp_path: Path, capsys) -> None:
    f = _write_jsonl(tmp_path / "session.jsonl", CLAUDE_TRANSCRIPT)

    rc = main(["audit", "--transcript", str(f)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "PASS" in out and "claude-code:session.jsonl" in out


def test_cli_transcript_format_flag(tmp_path: Path, capsys) -> None:
    f = tmp_path / "history.txt"
    f.write_text(AIDER_HISTORY.format(sha="a1b2c3d"), encoding="utf-8")

    rc = main(["audit", "--transcript", str(f), "--format", "aider"])

    assert rc in (0, 1)
    assert "aider:history.txt" in capsys.readouterr().out


def test_cli_transcript_unknown_format_errors(tmp_path: Path, capsys) -> None:
    f = tmp_path / "history.txt"
    f.write_text("noise\n", encoding="utf-8")

    rc = main(["audit", "--transcript", str(f)])

    assert rc == 2
    assert "--format" in capsys.readouterr().err


# --- Devin MCP adapter (cloud sessions) -------------------------------------

IN, OUT = "<" * 3, ">" * 3

MCP_LIST = f"""\
Total: 5
Showing 5
No more results (last page).

[event-aaa1] 2026-10-06 14:44:45 UTC {IN} initial_user_message (message): user: create the file src/marker.txt
[event-aaa2] 2026-10-06 14:44:46 UTC {OUT} shell_process_started (shell): exec: echo hi; touch src/marker.txt (shell: s1)
[event-aaa3] 2026-10-06 14:44:46 UTC {OUT} terminal_update (shell): terminal output update (shell: s1)
[event-aaa4] 2026-10-06 14:44:47 UTC {OUT} shell_process_completed (shell): exit_code=0, output: hi
[event-aaa5] 2026-10-06 14:44:48 UTC {OUT} devin_message (message): devin: I created src/marker.txt
"""


def _mcp_details(exit_code: str) -> str:
    return f"""\
Event details (5 events):

--- event-aaa1 ---
  type: initial_user_message (message)
  direction: incoming
  created_at: 2026-10-06 14:44:45 UTC
  contents: {{
  "type": "initial_user_message",
  "message": "create the file src/marker.txt",
  "timestamp": "2026-10-06T14:44:45.000000Z"
}}

--- event-aaa2 ---
  type: shell_process_started (shell)
  direction: outgoing
  created_at: 2026-10-06 14:44:46 UTC
  contents: {{
  "type": "shell_process_started",
  "command": "echo hi; touch src/marker.txt",
  "shell_id": "s1",
  "process_id": "proc-1",
  "starting_dir": "/home/ubuntu",
  "is_major_action": true
}}

--- event-aaa3 ---
  type: terminal_update (shell)
  direction: outgoing
  created_at: 2026-10-06 14:44:46 UTC
  contents: {{
  "type": "terminal_update",
  "contents": "aGkK",
  "process_id": "proc-1"
}}

--- event-aaa4 ---
  type: shell_process_completed (shell)
  direction: outgoing
  created_at: 2026-10-06 14:44:47 UTC
  contents: {{
  "type": "shell_process_completed",
  "exit_code": "{exit_code}",
  "output_trunc": "hi\\n",
  "process_id": "proc-1"
}}

--- event-aaa5 ---
  type: devin_message (message)
  direction: outgoing
  created_at: 2026-10-06 14:44:48 UTC
  contents: {{
  "type": "devin_message",
  "message": "I created src/marker.txt",
  "timestamp": "2026-10-06T14:44:48.000000Z"
}}
"""


MCP_META = "Session abc:\n  session_id: abc\n  title: Marker Probe\n  status: running\n"


def _fake_mcp_client(exit_code: str = "0"):
    def client(name, args, *, api_key, base_url, _rid=None):
        if name == "devin_session_interact":
            return MCP_META
        if args.get("action") == "list":
            return MCP_LIST
        return _mcp_details(exit_code)
    return client


def test_mcp_cloud_session_verifies(tmp_path: Path, monkeypatch) -> None:
    from devin_qa_pack.adapters import mcp

    monkeypatch.setenv("DEVIN_API_KEY", "cog_test")
    view = mcp.load("abc", _client=_fake_mcp_client())

    assert view.title == "Marker Probe"
    assert len(view.calls) == 1
    call = view.calls[0]
    assert call.kind == "execute" and call.status == "completed"
    assert any("touch src/marker.txt" in c for c in call.commands)
    assert "hi" in call.search_text  # base64 terminal output decoded

    audit = audit_source(view)
    assert audit.verdict == PASS
    file_claim = next(r for r in audit.results if r.claim.kind == "file")
    assert file_claim.status == VERIFIED


def test_mcp_failed_exit_disputes(monkeypatch) -> None:
    from devin_qa_pack.adapters import mcp

    monkeypatch.setenv("DEVIN_API_KEY", "cog_test")
    view = mcp.load("abc", _client=_fake_mcp_client(exit_code="1"))

    assert view.calls[0].status == "failed"
    audit = audit_source(view)
    assert audit.verdict == PARTIAL
    assert audit.results[0].status != VERIFIED


def test_mcp_missing_key_errors(monkeypatch) -> None:
    from devin_qa_pack.adapters import mcp

    monkeypatch.delenv("DEVIN_API_KEY", raising=False)
    try:
        mcp.load("abc", _client=_fake_mcp_client())
        raise AssertionError("expected McpError")
    except mcp.McpError as e:
        assert "DEVIN_API_KEY" in str(e)


def test_cli_source_mcp_needs_session(capsys) -> None:
    rc = main(["audit", "--source", "mcp", "--all"])
    assert rc == 2
    assert "--session" in capsys.readouterr().err
