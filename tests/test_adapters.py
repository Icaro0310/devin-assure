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
