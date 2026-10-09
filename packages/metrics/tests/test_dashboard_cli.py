"""cli: argparse wiring, exit codes, build/data contracts."""

from __future__ import annotations

import json

import pytest

from devin_metrics.dashboard.cli import main


def _args(sessions_db, acp_dir, *extra):
    return [
        "--sessions-db", str(sessions_db),
        "--acp-dir", str(acp_dir),
        *extra,
    ]


def test_build_writes_file(sessions_db, acp_dir, tmp_path, capsys):
    out = tmp_path / "index.html"
    rc = main(["build", *_args(sessions_db, acp_dir), "--out", str(out)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "application/json" in html
    capsys.readouterr()


def test_build_default_out_is_index_html(sessions_db, acp_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(["build", *_args(sessions_db, acp_dir)])
    assert rc == 0
    assert (tmp_path / "index.html").is_file()
    capsys.readouterr()


def test_data_json_dumps_stats_dict(sessions_db, acp_dir, capsys):
    rc = main(["data", *_args(sessions_db, acp_dir), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["tool"] == "devin-dashboard"
    assert data["summary"]["sessions"] == 4
    assert data["summary"]["cost_usd_total"] == pytest.approx(0.542)


def test_data_plain_prints_digest(sessions_db, acp_dir, capsys):
    rc = main(["data", *_args(sessions_db, acp_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "4" in out and "session" in out.lower()


def test_missing_acp_dir_warns_but_succeeds(sessions_db, tmp_path, capsys):
    rc = main(["data", "--sessions-db", str(sessions_db),
               "--acp-dir", str(tmp_path / "nope"), "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "acp" in captured.err.lower()
    assert json.loads(captured.out)["summary"]["cost_usd_total"] is None


def test_missing_sessions_db_fails(tmp_path, capsys):
    rc = main(["build", "--sessions-db", str(tmp_path / "nope.db"),
               "--acp-dir", str(tmp_path / "nope")])
    captured = capsys.readouterr()
    assert rc == 1
    assert "error" in captured.err.lower()


def test_data_dir_derives_both_paths(data_dir, tmp_path, capsys):
    out = tmp_path / "dash.html"
    rc = main(["build", "--data-dir", str(data_dir), "--out", str(out)])
    assert rc == 0
    assert "application/json" in out.read_text(encoding="utf-8")
    capsys.readouterr()
