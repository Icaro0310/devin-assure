"""collect: stats dict shape, exact aggregation math, degradation rules."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from devin_metrics.dashboard.collect import collect_stats
from devin_internals.schema import SchemaError

from tests.conftest import DAY_MS, T0


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def test_stats_dict_shape(stats):
    assert set(stats) >= {
        "tool", "version", "source", "summary",
        "daily", "projects", "models", "top_sessions",
    }
    assert stats["tool"] == "devin-dashboard"
    src = stats["source"]
    assert src["schema_version"] == 17
    assert src["acp_available"] is True
    assert src["acp_db_files"] == 4
    assert src["matched_sessions"] == 3
    assert src["orphan_dbs"] == 1
    # the whole dict must be JSON-serializable — it is embedded into HTML
    json.dumps(stats)


def test_summary_numbers(stats):
    s = stats["summary"]
    assert s["sessions"] == 4
    assert s["hidden_sessions"] == 0
    assert s["messages"] == 18
    assert s["tool_calls"] == 8
    assert s["duration_ms_total"] == 10_800_000
    assert s["cost_usd_total"] == pytest.approx(0.542)
    assert s["cost_usd_by_sessions"] == pytest.approx(0.535)
    assert s["input_tokens_total"] == 1420
    assert s["output_tokens_total"] == 542
    assert s["first_seen"] == _day(T0)
    assert s["last_seen"] == _day(T0 + 2 * DAY_MS)


def test_daily_rows(stats):
    daily = stats["daily"]
    assert [d["date"] for d in daily] == [
        _day(T0), _day(T0 + DAY_MS), _day(T0 + 2 * DAY_MS)
    ]
    day0, day1, day2 = daily
    assert day0["sessions"] == 2 and day0["cost_usd"] == pytest.approx(0.035)
    assert day1["sessions"] == 1 and day1["cost_usd"] == pytest.approx(0.5)
    assert day2["sessions"] == 1 and day2["cost_usd"] is None


def test_projects_rows(stats):
    projects = {r["project"]: r for r in stats["projects"]}
    assert set(projects) == {"/work/alpha", "/work/beta"}
    alpha = projects["/work/alpha"]
    assert alpha["sessions"] == 2
    assert alpha["messages"] == 9
    assert alpha["tool_calls"] == 4
    assert alpha["duration_ms"] == 1_800_000
    assert alpha["cost_usd"] == pytest.approx(0.035)
    assert alpha["input_tokens"] == 350
    beta = projects["/work/beta"]
    assert beta["cost_usd"] == pytest.approx(0.5)
    assert beta["input_tokens"] == 1000
    assert beta["output_tokens"] == 500


def test_models_rows(stats):
    models = {m["model"]: m for m in stats["models"]}
    assert set(models) == {"swe-2-high", "gpt-5.2"}
    swe = models["swe-2-high"]
    assert swe["sessions"] == 3
    assert swe["usage_records"] == 4  # 3 matched rows + 1 orphan row
    assert swe["cost_usd"] == pytest.approx(0.042)
    gpt = models["gpt-5.2"]
    assert gpt["sessions"] == 1
    assert gpt["cost_usd"] == pytest.approx(0.5)


def test_top_sessions(stats):
    top = stats["top_sessions"]
    longest = top["longest"]
    costliest = top["costliest"]
    assert longest[0]["title"] == "beta-1"
    assert longest[0]["duration_ms"] == 7_200_000
    assert costliest[0]["title"] == "beta-1"
    assert costliest[0]["cost_usd"] == pytest.approx(0.5)
    # beta-2 has no acp db -> excluded from costliest entirely
    assert all(s["cost_usd"] is not None for s in costliest)
    assert len(longest) <= 5 and len(costliest) <= 5


def test_missing_acp_dir_degrades(sessions_db, tmp_path):
    stats = collect_stats(sessions_db, tmp_path / "nope")
    assert stats["source"]["acp_available"] is False
    assert stats["source"]["acp_db_files"] == 0
    assert stats["summary"]["sessions"] == 4
    assert stats["summary"]["cost_usd_total"] is None
    assert stats["top_sessions"]["costliest"] == []
    assert all(d["cost_usd"] is None for d in stats["daily"])


def test_missing_sessions_db_fails(tmp_path):
    with pytest.raises(SchemaError):
        collect_stats(tmp_path / "nope.db", None)
