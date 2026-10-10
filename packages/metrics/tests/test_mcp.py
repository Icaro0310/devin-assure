"""MCP adapter contract: do_query output == `devin-metrics <kind>
--json` on the synthetic fixtures; store failures the CLI maps to exit
1 surface as {error, detail} — the tool never raises."""

import json
from pathlib import Path

import pytest
import tomllib
from devin_metrics.mcp_server import do_query


def _cli_json(capsys, argv):
    from devin_metrics.cli import main

    rc = main([*argv, "--json"])
    assert rc == 0
    return json.loads(capsys.readouterr().out)


def test_do_query_summary_matches_cli(sessions_db, acp_dir, capsys):
    expected = _cli_json(capsys, [
        "summary", "--sessions-db", str(sessions_db),
        "--acp-dir", str(acp_dir)])
    out = do_query("summary", sessions_db=str(sessions_db),
                   acp_dir=str(acp_dir))
    assert out == expected


def test_do_query_projects_matches_cli(sessions_db, acp_dir, capsys):
    expected = _cli_json(capsys, [
        "projects", "--sessions-db", str(sessions_db),
        "--acp-dir", str(acp_dir)])
    out = do_query("projects", sessions_db=str(sessions_db),
                   acp_dir=str(acp_dir))
    assert out == expected


def test_do_query_daily_matches_cli(sessions_db, acp_dir, capsys):
    expected = _cli_json(capsys, [
        "daily", "--days", "2", "--sessions-db", str(sessions_db),
        "--acp-dir", str(acp_dir)])
    out = do_query("daily", days=2, sessions_db=str(sessions_db),
                   acp_dir=str(acp_dir))
    assert out == expected


def test_do_query_daily_days_zero_means_all(sessions_db, acp_dir, capsys):
    expected = _cli_json(capsys, [
        "daily", "--sessions-db", str(sessions_db),
        "--acp-dir", str(acp_dir)])
    out = do_query("daily", days=0, sessions_db=str(sessions_db),
                   acp_dir=str(acp_dir))
    assert out == expected


def test_do_query_daily_omitted_days_beyond_thirty(tmp_path, acp_dir,
                                                  capsys):
    """Regression for the omitted-``days`` default: 35 distinct
    activity days must all come back — a hidden 30-day cap would
    truncate the result."""
    import sqlite3

    from conftest import DAY_MS, T0, _add_session
    from devin_internals.fixtures import create_sessions_db

    db = create_sessions_db(tmp_path / "sessions.db")
    con = sqlite3.connect(db)
    with con:
        con.execute("DELETE FROM sessions")
        for i in range(35):
            _add_session(con, f"day-{i}", (
                "/work/wide", "swe-2-high", f"day {i}",
                T0 + i * DAY_MS, T0 + i * DAY_MS + 600_000, 2, 1))
    expected = _cli_json(capsys, [
        "daily", "--sessions-db", str(db), "--acp-dir", str(acp_dir)])
    out = do_query("daily", sessions_db=str(db), acp_dir=str(acp_dir))
    assert out == expected
    assert len(out) == 35


def test_do_query_missing_acp_degrades(sessions_db, tmp_path):
    out = do_query("summary", sessions_db=str(sessions_db),
                   acp_dir=str(tmp_path / "no-acp"))
    assert out["acp_available"] is False
    assert out["cost_usd_total"] is None


def test_do_query_missing_db_is_error_not_raise(tmp_path):
    out = do_query("summary", sessions_db=str(tmp_path / "nope.db"))
    assert out["error"] == "bad_store"


def test_do_query_unknown_kind_is_error_not_raise(sessions_db, acp_dir):
    out = do_query("bogus", sessions_db=str(sessions_db),
                   acp_dir=str(acp_dir))
    assert out["error"] == "usage"


def test_server_entrypoint_in_pyproject():
    meta = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"))
    assert (meta["project"]["scripts"]["devin-metrics-mcp"]
            == "devin_metrics.mcp_server:main")
    assert any(dep.startswith("mcp") for dep in
               meta["project"]["optional-dependencies"]["mcp"])


def test_build_server_registers_tool():
    pytest.importorskip("mcp")
    from devin_metrics.mcp_server import build_server
    server = build_server()
    assert server is not None
