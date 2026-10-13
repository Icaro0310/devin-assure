"""``digest --weekly`` — composed report + week-key incrementality.

Covers the spec envelope (devin-powerups/docs/incrementality.md): a second
run in the same ISO week is a no-op via the ``metrics-digest`` cursor,
corrupt state fails open into a full run, and ``DEVIN_CURSOR_DIR``
redirects the state file.
"""

import json
from datetime import datetime, timezone

from devin_metrics.cli import main
from devin_metrics.digest import iso_week_key, report_name


def _week() -> str:
    return iso_week_key(datetime.now(timezone.utc))


def _digest_args(data_dir, out_dir, *extra):
    return ["digest", "--weekly", "--data-dir", str(data_dir),
            "--out-dir", str(out_dir), *extra]


def test_digest_writes_report_with_sections(
        data_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DEVIN_CURSOR_DIR", str(tmp_path / "cursor"))
    out_dir = tmp_path / "out"
    assert main(_digest_args(data_dir, out_dir)) == 0

    report = out_dir / report_name(_week())
    assert str(report) in capsys.readouterr().out  # path is printed
    text = report.read_text(encoding="utf-8")
    assert "weekly digest" in text and _week() in text
    # composed sections: summary + daily + watch
    assert "## devin-metrics summary" in text
    assert "## Daily activity" in text
    assert "## Watch" in text
    # real fixture numbers flowed through (4 sessions, alpha/beta days)
    assert "| Sessions" in text
    assert "| DATE" in text
    # atomic write: no tmp leftovers
    assert [p.name for p in out_dir.iterdir()] == [report.name]


def test_second_run_same_week_is_noop(
        data_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DEVIN_CURSOR_DIR", str(tmp_path / "cursor"))
    out_dir = tmp_path / "out"
    assert main(_digest_args(data_dir, out_dir)) == 0
    capsys.readouterr()
    report = out_dir / report_name(_week())
    first = report.read_bytes()

    assert main(_digest_args(data_dir, out_dir)) == 0
    out = capsys.readouterr().out
    assert "already digested" in out
    assert str(report) in out
    assert report.read_bytes() == first  # untouched

    assert main(_digest_args(data_dir, out_dir, "--force")) == 0
    assert "wrote" in capsys.readouterr().out


def test_corrupt_state_fails_open_into_full_run(
        data_dir, tmp_path, monkeypatch, capsys):
    cursor_dir = tmp_path / "cursor"
    cursor_dir.mkdir()
    state_file = cursor_dir / "metrics-digest-state.json"
    state_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("DEVIN_CURSOR_DIR", str(cursor_dir))

    out_dir = tmp_path / "out"
    assert main(_digest_args(data_dir, out_dir)) == 0  # no crash
    assert "wrote" in capsys.readouterr().out
    assert (out_dir / report_name(_week())).is_file()
    # cursor recovered into a valid state file
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["last_week"] == _week()


def test_cursor_dir_env_override(data_dir, tmp_path, monkeypatch, capsys):
    cursor_dir = tmp_path / "elsewhere"
    monkeypatch.setenv("DEVIN_CURSOR_DIR", str(cursor_dir))
    assert main(_digest_args(data_dir, tmp_path / "out")) == 0
    capsys.readouterr()

    state_file = cursor_dir / "metrics-digest-state.json"
    assert state_file.is_file()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["last_week"] == _week()
    assert state["watermark"] > 0  # max last_activity_at of the fixture


def test_since_backfills_an_explicit_week(
        data_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DEVIN_CURSOR_DIR", str(tmp_path / "cursor"))
    out_dir = tmp_path / "out"
    assert main(_digest_args(data_dir, out_dir, "--since", "2026-W40")) == 0
    assert (out_dir / "digest-2026-W40.md").is_file()
    # ISO dates resolve to the containing week too
    assert main(_digest_args(data_dir, out_dir,
                             "--since", "2026-10-05")) == 0
    assert (out_dir / "digest-2026-W41.md").is_file()


def test_digest_json_output(data_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DEVIN_CURSOR_DIR", str(tmp_path / "cursor"))
    assert main(_digest_args(data_dir, tmp_path / "out", "--json")) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["week"] == _week()
    assert rep["written"] is True
    assert rep["report"].endswith(report_name(_week()))
