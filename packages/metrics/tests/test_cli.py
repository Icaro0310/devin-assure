"""cli: argparse wiring, exit codes, --json contracts."""

from __future__ import annotations

import json

import pytest
from devin_metrics.cli import main


def _args(sessions_db, acp_dir, *extra):
    return [
        "--sessions-db", str(sessions_db),
        "--acp-dir", str(acp_dir),
        *extra,
    ]


def test_summary_markdown(sessions_db, acp_dir, capsys):
    rc = main(["summary", *_args(sessions_db, acp_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Sessions" in out or "sessions" in out
    assert "$0.5420" in out


def test_summary_json(sessions_db, acp_dir, capsys):
    rc = main(["summary", *_args(sessions_db, acp_dir, "--json")])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["sessions"] == 4
    assert data["cost_usd_total"] == pytest.approx(0.542)


def test_projects(sessions_db, acp_dir, capsys):
    rc = main(["projects", *_args(sessions_db, acp_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "/work/alpha" in out and "/work/beta" in out


def test_projects_json(sessions_db, acp_dir, capsys):
    rc = main(["projects", *_args(sessions_db, acp_dir, "--json")])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert isinstance(data, list) and len(data) == 2


def test_daily_days_filter(sessions_db, acp_dir, capsys):
    rc = main(["daily", *_args(sessions_db, acp_dir, "--days", "1", "--json")])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert len(data) == 1


def test_daily_all_days(sessions_db, acp_dir, capsys):
    rc = main(["daily", *_args(sessions_db, acp_dir, "--json")])
    assert rc == 0
    assert len(json.loads(capsys.readouterr().out)) == 3


def test_missing_acp_dir_warns_but_succeeds(sessions_db, tmp_path, capsys):
    rc = main(["summary", "--sessions-db", str(sessions_db),
               "--acp-dir", str(tmp_path / "nope")])
    captured = capsys.readouterr()
    assert rc == 0
    assert "acp" in captured.err.lower()
    assert "Sessions" in captured.out or "sessions" in captured.out


def test_missing_sessions_db_fails(tmp_path, capsys):
    rc = main(["summary", "--sessions-db", str(tmp_path / "nope.db")])
    captured = capsys.readouterr()
    assert rc == 1
    assert "error" in captured.err.lower()


def test_data_dir_derives_both_paths(data_dir, capsys):
    rc = main(["summary", "--data-dir", str(data_dir), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["sessions"] == 4
    assert data["cost_usd_total"] == pytest.approx(0.542)
