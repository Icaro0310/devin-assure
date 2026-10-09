"""aggregate: rollups over a MetricsSnapshot — math asserted exactly."""

from __future__ import annotations

import pytest

from devin_metrics.aggregate import (
    by_day,
    by_model,
    by_project,
    summarize,
    top_sessions,
)
from devin_metrics.collect import collect


@pytest.fixture
def snap(sessions_db, acp_dir):
    return collect(sessions_db, acp_dir)


def _day_of(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


# -- summarize ---------------------------------------------------------------


def test_summary_totals(snap):
    s = summarize(snap)
    assert s["sessions"] == 4
    assert s["sessions_with_cost"] == 3
    assert s["messages"] == 18
    assert s["tool_calls"] == 8
    assert s["duration_ms_total"] == 10_800_000
    # total includes the orphan acp db; attributed does not
    assert s["cost_usd_total"] == pytest.approx(0.542)
    assert s["cost_usd_by_sessions"] == pytest.approx(0.535)
    assert s["input_tokens_total"] == 1420
    assert s["output_tokens_total"] == 542


def test_summary_averages(snap):
    s = summarize(snap)
    assert s["avg_duration_ms"] == pytest.approx(10_800_000 / 4)
    assert s["avg_messages"] == pytest.approx(18 / 4)
    assert s["avg_cost_usd"] == pytest.approx(0.535 / 3)


def test_summary_date_range(snap):
    from tests.conftest import DAY_MS, T0

    s = summarize(snap)
    assert s["first_seen"] == _day_of(T0)
    assert s["last_seen"] == _day_of(T0 + 2 * DAY_MS)


def test_summary_top_lists(snap):
    s = summarize(snap)
    longest = s["top_longest"]
    assert longest[0]["duration_ms"] == 7_200_000
    costliest = s["top_costliest"]
    assert costliest[0]["cost_usd"] == pytest.approx(0.5)
    assert all(c["cost_usd"] is not None for c in costliest)


def test_summary_empty_snapshot():
    from devin_metrics.collect import MetricsSnapshot

    empty = MetricsSnapshot(
        sessions_db="-", acp_dir=None, acp_available=False,
        acp_db_files=0, acp_db_errors=0, matched_sessions=0,
        orphan_dbs=0, sessions=(), usage=(),
    )
    s = summarize(empty)
    assert s["sessions"] == 0
    assert s["avg_cost_usd"] is None
    assert s["first_seen"] is None


# -- by_project --------------------------------------------------------------


def test_by_project_grouping(snap):
    rows = {r["project"]: r for r in by_project(snap)}
    assert set(rows) == {"/work/alpha", "/work/beta"}
    alpha = rows["/work/alpha"]
    assert alpha["sessions"] == 2
    assert alpha["messages"] == 9
    assert alpha["tool_calls"] == 4
    assert alpha["duration_ms"] == 1_800_000
    assert alpha["cost_usd"] == pytest.approx(0.035)
    beta = rows["/work/beta"]
    assert beta["cost_usd"] == pytest.approx(0.5)
    assert beta["input_tokens"] == 1000


def test_by_project_sorted_by_cost_desc(snap):
    rows = by_project(snap)
    assert rows[0]["project"] == "/work/beta"  # 0.5 > 0.035


# -- by_model ----------------------------------------------------------------


def test_by_model_merges_declared_and_usage(snap):
    rows = {r["model"]: r for r in by_model(snap)}
    swe = rows["swe-2-high"]
    assert swe["sessions"] == 3          # declared on session rows
    assert swe["usage_records"] == 4     # incl. the orphan acp db row
    assert swe["cost_usd"] == pytest.approx(0.042)
    assert swe["input_tokens"] == 420
    gpt = rows["gpt-5.2"]
    assert gpt["sessions"] == 1
    assert gpt["cost_usd"] == pytest.approx(0.5)


# -- by_day ------------------------------------------------------------------


def test_by_day_buckets(snap):
    from tests.conftest import DAY_MS, T0

    rows = {r["date"]: r for r in by_day(snap)}
    assert len(rows) == 3
    d1 = rows[_day_of(T0)]
    assert d1["sessions"] == 2
    assert d1["cost_usd"] == pytest.approx(0.035)
    d2 = rows[_day_of(T0 + DAY_MS)]
    assert d2["sessions"] == 1
    assert d2["cost_usd"] == pytest.approx(0.5)
    d3 = rows[_day_of(T0 + 2 * DAY_MS)]
    assert d3["sessions"] == 1
    assert d3["cost_usd"] is None  # session has no acp db


def test_by_day_days_filter_keeps_most_recent(snap):
    from tests.conftest import DAY_MS, T0

    rows = by_day(snap, days=1)
    assert len(rows) == 1
    assert rows[0]["date"] == _day_of(T0 + 2 * DAY_MS)
    assert len(by_day(snap, days=2)) == 2
    assert len(by_day(snap, days=99)) == 3


# -- top_sessions ------------------------------------------------------------


def test_top_sessions_duration(snap):
    rows = top_sessions(snap, n=2, key="duration_ms")
    assert [r["duration_ms"] for r in rows] == [7_200_000, 1_800_000]
    assert rows[0]["project"] == "/work/beta"


def test_top_sessions_cost_skips_unknown(snap):
    rows = top_sessions(snap, n=10, key="cost_usd")
    assert len(rows) == 3  # only sessions with known cost
    assert rows[0]["cost_usd"] == pytest.approx(0.5)
