"""Fixtures — generated synthetically via devin_internals.fixtures.

No real Devin data is ever touched: the DDL is the real v17 layout (copied by
``devin_internals.fixtures``), every row inserted here is synthetic and
deterministic so aggregation tests can assert exact numbers.

Layout produced by ``data_dir``::

    <root>/cli/sessions.db                  4 sessions, controlled fields
    <root>/User/acp-messages/<sid>.db       3 matched + 1 orphan db

Crafted ACP ``messages`` rows use the assumed payload shape documented in
``docs/SCHEMA.md``::

    {"model": "<id>", "cost_usd": <float>,
     "usage": {"input_tokens": <int>, "output_tokens": <int>}}
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from devin_internals.fixtures import (
    create_acp_messages_db,
    create_sessions_db,
)

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
T0 = 1_780_000_000_000  # matches devin_internals.fixtures._BASE_TS_MS

# (working_directory, model, title, created_at, last_activity_at, n_msgs, n_tools)
SESSION_PLAN = [
    ("/work/alpha", "swe-2-high", "alpha-1", T0, T0 + 600_000, 5, 3),
    ("/work/alpha", "swe-2-high", "alpha-2", T0 + HOUR_MS, T0 + HOUR_MS + 1_200_000, 4, 1),
    ("/work/beta", "gpt-5.2", "beta-1", T0 + DAY_MS, T0 + DAY_MS + 7_200_000, 7, 4),
    ("/work/beta", "swe-2-high", "beta-2", T0 + 2 * DAY_MS, T0 + 2 * DAY_MS + 1_800_000, 2, 0),
]

# acp usage rows per session index: list of (model, cost_usd, in_tok, out_tok)
USAGE_PLAN = {
    0: [("swe-2-high", 0.0100, 100, 10), ("swe-2-high", 0.0200, 200, 20)],
    1: [("swe-2-high", 0.0050, 50, 5)],
    2: [("gpt-5.2", 0.5000, 1000, 500)],
    # session 3: intentionally no acp db -> cost/tokens stay None
}
ORPHAN_USAGE = [("swe-2-high", 0.0070, 70, 7)]


def _add_session(con: sqlite3.Connection, sid: str, plan: tuple) -> None:
    wd, model, title, created, last, _n_msgs, _n_tools = plan
    con.execute(
        "INSERT INTO sessions(id, working_directory, backend_type, model,"
        " agent_mode, created_at, last_activity_at, title, main_chain_id,"
        " shell_last_seen_index, cogs_json, workspace_dirs, hidden, metadata)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sid, wd, "fixture-backend", model, "fixture-mode",
            created, last, title, 1, 0,
            json.dumps({"synthetic": True}),
            json.dumps([wd]),
            0,
            json.dumps({"synthetic": True}),
        ),
    )


def _add_children(con: sqlite3.Connection, sid: str, n_msgs: int, n_tools: int) -> None:
    for node in range(1, n_msgs + 1):
        con.execute(
            "INSERT INTO message_nodes(session_id, node_id, parent_node_id,"
            " chat_message, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, node, None if node == 1 else node - 1,
             json.dumps({"synthetic": True}), T0 + node * 1_000, None),
        )
    for tc in range(n_tools):
        con.execute(
            "INSERT INTO tool_call_state(session_id, tool_call_id,"
            " tool_call_json, tool_call_update_json) VALUES (?, ?, ?, ?)",
            (sid, f"{sid}-tc-{tc}", json.dumps({"synthetic": True}), None),
        )


def _usage_payload(model: str, cost: float, in_tok: int, out_tok: int) -> str:
    return json.dumps({
        "model": model,
        "cost_usd": cost,
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    })


def _write_acp_db(path: Path, rows: list[tuple]) -> None:
    create_acp_messages_db(path)
    con = sqlite3.connect(path)
    with con:
        con.execute("DELETE FROM messages")
        pos = 0
        for model, cost, in_tok, out_tok in rows:
            con.execute(
                "INSERT INTO messages(position, kind, payload) VALUES (?, ?, ?)",
                (pos, "agent.message", _usage_payload(model, cost, in_tok, out_tok)),
            )
            pos += 1
        con.execute(
            "INSERT INTO messages(position, kind, payload) VALUES (?, ?, ?)",
            (pos, "user.message", json.dumps({"text": "synthetic prompt"})),
        )
    con.close()


def _wipe_generated_rows(db: Path) -> None:
    con = sqlite3.connect(db)
    with con:
        for table in (
            "message_nodes", "tool_call_state", "prompt_history",
            "rendered_commits", "subagent_heads", "sessions",
        ):
            con.execute(f"DELETE FROM {table}")
    con.close()


@pytest.fixture
def session_ids() -> list[str]:
    return [str(uuid.uuid5(uuid.NAMESPACE_URL, f"devin-metrics-fixture-{i}"))
            for i in range(len(SESSION_PLAN))]


@pytest.fixture
def data_dir(tmp_path: Path, session_ids: list[str]) -> Path:
    """Synthetic Devin data dir with controlled sessions + acp usage rows."""
    root = tmp_path / "devin"
    sessions_db = create_sessions_db(root / "cli" / "sessions.db")
    _wipe_generated_rows(sessions_db)

    con = sqlite3.connect(sessions_db)
    with con:
        for sid, plan in zip(session_ids, SESSION_PLAN):
            _add_session(con, sid, plan)
            _add_children(con, sid, plan[5], plan[6])
    con.close()

    acp_dir = root / "User" / "acp-messages"
    for idx, rows in USAGE_PLAN.items():
        _write_acp_db(acp_dir / f"{session_ids[idx]}.db", rows)
    _write_acp_db(acp_dir / f"{uuid.uuid4()}.db", ORPHAN_USAGE)
    return root


@pytest.fixture
def sessions_db(data_dir: Path) -> Path:
    return data_dir / "cli" / "sessions.db"


@pytest.fixture
def acp_dir(data_dir: Path) -> Path:
    return data_dir / "User" / "acp-messages"


@pytest.fixture
def stats(sessions_db: Path, acp_dir: Path) -> dict:
    from devin_metrics.dashboard.collect import collect_stats

    return collect_stats(sessions_db, acp_dir)
