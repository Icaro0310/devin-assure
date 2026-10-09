"""render: markdown tables + JSON payloads."""

from __future__ import annotations

import json

import pytest

from devin_metrics.aggregate import by_day, by_model, by_project, summarize
from devin_metrics.collect import collect
from devin_metrics.render import (
    dumps_json,
    fmt_duration_ms,
    fmt_int,
    fmt_usd,
    md_table,
    render_daily_md,
    render_projects_md,
    render_summary_md,
)


@pytest.fixture
def snap(sessions_db, acp_dir):
    return collect(sessions_db, acp_dir)


def test_md_table_shape():
    out = md_table(["A", "B"], [["1", "x"], ["22", "yy"]])
    lines = out.splitlines()
    assert lines[0] == "| A  | B  |"
    assert set(lines[1]) <= {"-", "|", " "}
    assert lines[1].count("|") == 3
    assert lines[2] == "| 1  | x  |"


def test_fmt_helpers():
    assert fmt_usd(None) == "-"
    assert fmt_usd(0.5) == "$0.5000"
    assert fmt_int(None) == "-"
    assert fmt_int(1420) == "1,420"
    assert fmt_duration_ms(None) == "-"
    assert fmt_duration_ms(7_200_000) == "2h 00m"
    assert fmt_duration_ms(45_000) == "45s"


def test_render_summary_md(snap):
    md = render_summary_md(summarize(snap))
    assert "## devin-metrics summary" in md or "# " in md
    assert "$0.5420" in md  # total incl. orphan
    assert "swe-2-high" in md  # model table rendered
    assert "18" in md  # messages


def test_render_projects_md(snap):
    md = render_projects_md(by_project(snap))
    assert "| PROJECT" in md.upper() or "PROJECT" in md.upper()
    assert "/work/alpha" in md
    assert "/work/beta" in md


def test_render_daily_md(snap):
    md = render_daily_md(by_day(snap))
    assert md.count("\n|") >= 3  # header + 3 day rows
    assert "-" in md  # day-3 unknown cost renders as dash


def test_dumps_json_roundtrip(snap):
    payload = {
        "summary": summarize(snap),
        "projects": by_project(snap),
        "models": by_model(snap),
        "daily": by_day(snap),
    }
    data = json.loads(dumps_json(payload))
    assert data["summary"]["sessions"] == 4
    assert data["projects"][0]["project"] == "/work/beta"
    assert {r["model"] for r in data["models"]} == {"swe-2-high", "gpt-5.2"}
