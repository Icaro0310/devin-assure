"""MCP adapter contract: do_run returns the same report dict that
`devin-evals run` writes as report.json; failures the CLI maps to exit
code 2 surface as {error, detail} — the tool never raises. The module
never imports the session-spawning paths."""

import json
from pathlib import Path

import pytest
import tomllib
from conftest import SESSION_TITLE, write_eval
from devin_evals.mcp_server import do_run
from devin_evals.runner import run_evals

PASS_CASE = {
    "id": "mcp-ok",
    "description": "mcp smoke",
    "session_ref": SESSION_TITLE,
    "rubric": [{"grader": "contains", "text": "all tests pass"}],
}
FAIL_CASE = {
    "id": "mcp-fail",
    "description": "mcp fail",
    "session_ref": SESSION_TITLE,
    "rubric": [{"grader": "contains", "text": "never produced"}],
}


def test_do_run_matches_run_evals_and_report_json(
        evals_dir, sessions_db, tmp_path):
    write_eval(evals_dir, "a", PASS_CASE)
    out = do_run(str(evals_dir), str(sessions_db))
    expected = run_evals(evals_dir, sessions_db, out_dir=tmp_path / "o")
    on_disk = json.loads(
        (tmp_path / "o" / "report.json").read_text(encoding="utf-8"))
    assert out == expected == on_disk


def test_do_run_summary_counts(evals_dir, sessions_db):
    write_eval(evals_dir, "a", PASS_CASE)
    write_eval(evals_dir, "b", FAIL_CASE)
    write_eval(evals_dir, "c", {**PASS_CASE, "id": "mcp-skip",
                                "session_ref": None})
    out = do_run(str(evals_dir), str(sessions_db))
    s = out["summary"]
    assert (s["total"], s["passed"], s["failed"], s["skipped"],
            s["errored"]) == (3, 1, 1, 1, 0)
    statuses = {c["id"]: c["status"] for c in out["cases"]}
    assert statuses == {"mcp-ok": "pass", "mcp-fail": "fail",
                        "mcp-skip": "skip"}


def test_do_run_bad_evals_dir_is_error_not_raise(tmp_path, sessions_db):
    out = do_run(str(tmp_path / "nope"), str(sessions_db))
    assert out["error"] == "bad_evals"


def test_do_run_missing_db_is_error_not_raise(evals_dir, tmp_path):
    write_eval(evals_dir, "a", PASS_CASE)
    out = do_run(str(evals_dir), str(tmp_path / "nope.db"))
    assert out["error"] == "bad_store"


def test_module_never_reaches_session_spawning_paths():
    src = (Path(__file__).parents[1] / "src" / "devin_evals"
           / "mcp_server.py").read_text(encoding="utf-8").lower()
    for banned in ("judge", "abrun", "ab-run", "corpus", "dream"):
        assert banned not in src


def test_server_entrypoint_in_pyproject():
    meta = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"))
    assert (meta["project"]["scripts"]["devin-evals-mcp"]
            == "devin_evals.mcp_server:main")
    assert any(dep.startswith("mcp") for dep in
               meta["project"]["optional-dependencies"]["mcp"])


def test_build_server_registers_tool():
    """The registered tool must reach the MCP surface under its published
    name — a helper-based registration or a decorator rename would leave
    the skill calling a tool that does not exist."""
    pytest.importorskip("mcp")
    import asyncio
    import inspect

    from devin_evals.mcp_server import build_server

    server = build_server()
    list_tools = getattr(server, "list_tools", None)
    if callable(list_tools):
        tools = list_tools()
        if inspect.isawaitable(tools):
            tools = asyncio.run(tools)
        names = {getattr(t, "name", t) for t in tools}
    else:  # tool-manager internals differ across SDK versions
        manager = getattr(server, "_tool_manager", None) or getattr(
            server, "tools", None)
        assert manager is not None
        names = set(getattr(manager, "_tools", manager))
    assert "evals_run" in names


def _registered_tool_names() -> set[str]:
    """Tools the MCP server registers — derived statically so this test
    runs without the optional ``mcp`` extra installed. The pin covers the
    decorated function names; the published tool name is verified live by
    ``test_build_server_registers_tool``."""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).parents[1] / "src" / "devin_evals" / "mcp_server.py"
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
    assert _registered_tool_names() == {"evals_run"}
