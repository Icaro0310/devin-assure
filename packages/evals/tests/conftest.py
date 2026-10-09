"""Shared fixtures: synthetic ``sessions.db`` stores with controlled content.

Uses ``devin_internals.fixtures`` for the real v17 DDL + schema history, then
inserts sessions whose transcript / tool calls / workspace are fully known so
grader behaviour can be asserted exactly.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from devin_internals.fixtures import create_sessions_db

SESSION_ID = "eval-fixture-session"
SESSION_TITLE = "Eval fixture session"

# Secret-shaped strings (synthetic, match the vendored detector regexes).
FAKE_API_KEY = "sk-" + "a" * 20
FAKE_GH_TOKEN = "ghp_" + "b" * 20

_BASE_TS_MS = 1_780_000_000_000


def insert_session(
    db_path: Path,
    *,
    session_id: str = SESSION_ID,
    title: str = SESSION_TITLE,
    working_directory: str = ".",
    messages: tuple[tuple[str, str], ...] = (),
    prompts: tuple[str, ...] = (),
    tool_calls: tuple[tuple[str, dict, dict | None], ...] = (),
) -> None:
    """Append one fully-specified session to an existing fixture db."""
    con = sqlite3.connect(db_path)
    with con:
        con.execute(
            "INSERT INTO sessions(id, working_directory, backend_type, model,"
            " agent_mode, created_at, last_activity_at, title, main_chain_id,"
            " shell_last_seen_index, cogs_json, workspace_dirs, hidden, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                working_directory,
                "fixture-backend",
                "fixture-model",
                "fixture-mode",
                _BASE_TS_MS,
                _BASE_TS_MS + 120_000,
                title,
                1,
                0,
                None,
                json.dumps([working_directory]),
                0,
                None,
            ),
        )
        for i, (role, text) in enumerate(messages, start=1):
            con.execute(
                "INSERT INTO message_nodes(session_id, node_id, parent_node_id,"
                " chat_message, created_at, metadata)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    i,
                    None if i == 1 else i - 1,
                    json.dumps({"role": role, "text": text}),
                    _BASE_TS_MS + i * 10_000,
                    None,
                ),
            )
        for i, content in enumerate(prompts):
            con.execute(
                "INSERT INTO prompt_history(content, timestamp, session_id, is_shell)"
                " VALUES (?, ?, ?, ?)",
                (content, _BASE_TS_MS + i * 5_000, session_id, 0),
            )
        for call_id, call, update in tool_calls:
            con.execute(
                "INSERT INTO tool_call_state(session_id, tool_call_id,"
                " tool_call_json, tool_call_update_json) VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    call_id,
                    json.dumps(call),
                    None if update is None else json.dumps(update),
                ),
            )
    con.close()


def make_db(
    path: Path,
    *,
    working_directory: str = ".",
    messages: tuple[tuple[str, str], ...] = (),
    prompts: tuple[str, ...] = (),
    tool_calls: tuple[tuple[str, dict, dict | None], ...] = (),
    extra_sessions: tuple[dict, ...] = (),
) -> Path:
    """Create a fixture sessions.db plus one controlled session."""
    create_sessions_db(path, n_sessions=0)
    insert_session(
        path,
        working_directory=working_directory,
        messages=messages,
        prompts=prompts,
        tool_calls=tool_calls,
    )
    for spec in extra_sessions:
        insert_session(path, **spec)
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A session working directory with one known file."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "output.txt").write_text("fixture output\n", encoding="utf-8")
    (ws / "sub").mkdir()
    return ws


@pytest.fixture
def sessions_db(tmp_path: Path, workspace: Path) -> Path:
    """sessions.db whose controlled session exercises every grader."""
    return make_db(
        tmp_path / "sessions.db",
        working_directory=str(workspace),
        messages=(
            ("user", "Run the tests and publish the report."),
            ("agent", "all tests pass; report published"),
        ),
        prompts=("Run the tests and publish the report.",),
        tool_calls=(
            (
                "tc-1",
                {"name": "run_shell", "arguments": {"command": "pytest -q"}},
                {"status": "finished", "exit_code": 0},
            ),
            (
                "tc-2",
                {"name": "devin_redact", "arguments": {"path": "report.md"}},
                {"status": "finished", "exit_code": 0},
            ),
            (
                "tc-3",
                {"name": "publish_report", "arguments": {}},
                {"status": "finished", "exit_code": 0},
            ),
        ),
    )


@pytest.fixture
def evals_dir(tmp_path: Path) -> Path:
    d = tmp_path / "evals"
    d.mkdir()
    return d


def write_eval(evals_dir: Path, name: str, case: dict) -> Path:
    p = evals_dir / f"{name}.json"
    p.write_text(json.dumps(case, indent=2), encoding="utf-8")
    return p
