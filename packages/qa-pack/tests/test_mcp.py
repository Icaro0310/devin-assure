"""MCP adapter contract: do_audit mirrors `audit --json`; failures the
CLI maps to exit code 2 surface as {error, detail} — the tool never
raises."""

import json
from pathlib import Path

import pytest
import tomllib
from devin_qa_pack.mcp_server import do_audit


def _session(payload: dict, sid: str) -> dict:
    return next(s for s in payload["sessions"] if s["session_id"] == sid)


def test_do_audit_single_session_matches_cli(sessions_db, capsys):
    from devin_qa_pack.cli import main

    rc = main([
        "audit", "--session", "sess-verified",
        "--sessions-db", str(sessions_db), "--json",
    ])
    expected = json.loads(capsys.readouterr().out)
    out = do_audit(session_id="sess-verified", sessions_db=str(sessions_db))
    assert rc == 0
    assert out == expected


def test_do_audit_all_covers_fixture_sessions(sessions_db):
    out = do_audit(audit_all_sessions=True, sessions_db=str(sessions_db))
    verdicts = {s["session_id"]: s["verdict"] for s in out["sessions"]}
    assert verdicts["sess-verified"] == "PASS"
    assert verdicts["sess-disputed"] == "PARTIAL"
    assert verdicts["sess-unverifiable"] == "UNVERIFIED"


def test_do_audit_unknown_session_is_error_not_raise(sessions_db):
    out = do_audit(session_id="nope", sessions_db=str(sessions_db))
    assert out["error"] == "unknown_session"


def test_do_audit_missing_db(tmp_path):
    out = do_audit(sessions_db=str(tmp_path / "nope.db"))
    assert out["error"] == "no_store"


def test_do_audit_conflicting_scope_is_error(sessions_db):
    out = do_audit(
        session_id="sess-verified", audit_all_sessions=True,
        sessions_db=str(sessions_db))
    assert out["error"] == "conflicting_scope"


def test_do_audit_default_limit_covers_all(sessions_db):
    out = do_audit(audit_all_sessions=True, sessions_db=str(sessions_db))
    assert len(out["sessions"]) >= 3


def test_do_audit_default_limit_not_capped_at_twenty(tmp_path):
    """Boundary regression: the all-sessions default must not silently
    stop at the old 20-session cap (the CLI's limit=None)."""
    import sqlite3

    from conftest import add_session
    from devin_internals.fixtures import create_sessions_db
    db = create_sessions_db(tmp_path / "sessions.db", n_sessions=0)
    con = sqlite3.connect(db)
    with con:
        for i in range(25):
            add_session(con, f"bulk-{i}", "/nonexistent", f"bulk {i}")
    out = do_audit(audit_all_sessions=True, sessions_db=str(db))
    assert len(out["sessions"]) == 25


def test_do_audit_mcp_source_needs_session_id():
    out = do_audit(source="mcp")
    assert out["error"] == "usage"


def test_server_entrypoint_in_pyproject():
    meta = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"))
    assert (meta["project"]["scripts"]["devin-qa-pack-mcp"]
            == "devin_qa_pack.mcp_server:main")
    assert any(dep.startswith("mcp") for dep in
               meta["project"]["optional-dependencies"]["mcp"])


def test_build_server_registers_tool():
    pytest.importorskip("mcp")
    from devin_qa_pack.mcp_server import build_server
    server = build_server()
    assert server is not None


def _registered_tool_names() -> set[str]:
    """Tools the MCP server registers — derived statically so this test
    runs without the optional ``mcp`` extra installed."""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).parents[1] / "src" / "devin_qa_pack" / "mcp_server.py"
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "tool"
            for dec in node.decorator_list
        )
    }


def test_mcp_tool_surface_is_pinned():
    """Regression contract: the AI surface is exactly this set. A new
    tool only lands after a deliberate edit here — check it stays
    read-only before widening."""
    assert _registered_tool_names() == {"qa_audit"}
