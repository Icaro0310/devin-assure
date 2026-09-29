"""Fixtures — generated synthetically via devin_internals.fixtures.

``create_sessions_db()`` lays down the real v17 DDL with synthetic filler
rows; the helpers below extend it with *controlled* message_nodes carrying
deliverable claims and tool_call_state rows that confirm, contradict or
leave them unverifiable. No real Devin data is ever touched.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from devin_internals.fixtures import _BASE_TS_MS, create_sessions_db


def add_session(
    con: sqlite3.Connection,
    sid: str,
    working_directory: str,
    title: str | None = None,
    created: int = _BASE_TS_MS,
) -> None:
    con.execute(
        "INSERT INTO sessions(id, working_directory, backend_type, model, agent_mode,"
        " created_at, last_activity_at, title, main_chain_id, shell_last_seen_index,"
        " cogs_json, workspace_dirs, hidden, metadata)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sid,
            working_directory,
            "fixture-backend",
            "fixture-model",
            "fixture-mode",
            created,
            created + 120_000,
            title,
            1,
            0,
            json.dumps({"synthetic": True}),
            json.dumps([working_directory]),
            0,
            json.dumps({"synthetic": True}),
        ),
    )


def add_message(
    con: sqlite3.Connection,
    sid: str,
    node_id: int,
    role: str,
    text: str,
    created: int = _BASE_TS_MS,
) -> None:
    con.execute(
        "INSERT INTO message_nodes(session_id, node_id, parent_node_id, chat_message,"
        " created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
        (
            sid,
            node_id,
            node_id - 1 if node_id > 1 else None,
            json.dumps({"role": role, "text": text}),
            created + node_id * 10_000,
            json.dumps({"synthetic": True}),
        ),
    )


def add_tool_call(
    con: sqlite3.Connection,
    sid: str,
    tool_call_id: str,
    call: dict | None = None,
    update: dict | None = None,
) -> None:
    con.execute(
        "INSERT INTO tool_call_state(session_id, tool_call_id, tool_call_json,"
        " tool_call_update_json) VALUES (?, ?, ?, ?)",
        (
            sid,
            tool_call_id,
            json.dumps(call) if call is not None else None,
            json.dumps(update) if update is not None else None,
        ),
    )


def exec_call(command: str, status: str = "completed", output: str = "") -> dict:
    return {
        "kind": "execute",
        "title": command,
        "status": status,
        "rawInput": {"command": command},
        "rawOutput": {"output": output},
    }


@pytest.fixture
def sessions_db(tmp_path: Path) -> Path:
    """sessions.db with four purpose-built sessions:

    - ``sess-verified``: every claim backed by a successful tool call.
    - ``sess-disputed``: test/commit claims, no corroborating tool calls.
    - ``sess-unverifiable``: a file claim that cannot be checked (no tool
      calls, working dir not on disk).
    - ``sess-opaque``: a tests claim whose only tool-call row has NULL
      payloads (interrupted call — ground truth unreadable).
    """
    db = create_sessions_db(tmp_path / "sessions.db", n_sessions=0)
    missing_wd = str(tmp_path / "no-such-wd")
    con = sqlite3.connect(db)
    with con:
        add_session(con, "sess-verified", missing_wd, "Verified session")
        add_message(con, "sess-verified", 1, "user", "fix the flaky suite")
        add_message(
            con,
            "sess-verified",
            2,
            "agent",
            "All tests passed — pytest is green. I created src/report.html, "
            "committed a1b2c3d and pushed to origin/main.",
        )
        add_tool_call(con, "sess-verified", "tc-pytest",
                      exec_call("python -m pytest -q", output="12 passed"))
        add_tool_call(
            con,
            "sess-verified",
            "tc-write",
            {
                "kind": "edit",
                "title": "src/report.html",
                "status": "completed",
                "rawInput": {"path": "src/report.html"},
            },
        )
        add_tool_call(con, "sess-verified", "tc-commit",
                      exec_call("git commit -m fix", output="[main a1b2c3d] fix"))
        add_tool_call(con, "sess-verified", "tc-push",
                      exec_call("git push origin main"))

        add_session(con, "sess-disputed", missing_wd, "Disputed session",
                    created=_BASE_TS_MS + 3_600_000)
        add_message(
            con,
            "sess-disputed",
            1,
            "agent",
            "All tests passed. Committed deadbee.",
        )

        add_session(con, "sess-unverifiable", missing_wd, "Mystery session",
                    created=_BASE_TS_MS + 7_200_000)
        add_message(
            con,
            "sess-unverifiable",
            1,
            "agent",
            "I wrote docs/spec.pdf as requested.",
        )

        add_session(con, "sess-opaque", missing_wd, "Opaque session",
                    created=_BASE_TS_MS + 10_800_000)
        add_message(con, "sess-opaque", 1, "agent", "Tests passed.")
        add_tool_call(con, "sess-opaque", "tc-null")
    con.close()
    return db


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, str]:
    """A real git repo with one commit; returns ``(repo_path, head_sha)``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=fixture@example.com", "-c", "user.name=fixture",
         "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha
