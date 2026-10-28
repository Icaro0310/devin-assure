"""alerts --rules: threshold rules evaluated against the summary metrics."""

from __future__ import annotations

import json

import pytest
from devin_metrics.alerts import RulesError, evaluate, load_rules
from devin_metrics.cli import main


def _rules_file(tmp_path, rules):
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(rules), encoding="utf-8")
    return p


# -- load_rules validation -----------------------------------------------------


def test_load_rules_happy_path(tmp_path):
    p = _rules_file(tmp_path, [
        {"name": "cost", "metric": "cost_usd_total",
         "op": ">", "threshold": 1.0},
    ])
    rules = load_rules(p)
    assert rules == [
        {"name": "cost", "metric": "cost_usd_total",
         "op": ">", "threshold": 1.0},
    ]


def test_load_rules_rejects_missing_file(tmp_path):
    with pytest.raises(RulesError):
        load_rules(tmp_path / "gone.json")


def test_load_rules_rejects_bad_json(tmp_path):
    p = tmp_path / "rules.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(RulesError, match="not valid JSON"):
        load_rules(p)


def test_load_rules_rejects_non_list(tmp_path):
    p = _rules_file(tmp_path, {"name": "x"})
    with pytest.raises(RulesError, match="list"):
        load_rules(p)


def test_load_rules_rejects_bad_op(tmp_path):
    p = _rules_file(tmp_path, [
        {"name": "x", "metric": "sessions", "op": "~~", "threshold": 1},
    ])
    with pytest.raises(RulesError, match="op"):
        load_rules(p)


def test_load_rules_rejects_missing_key(tmp_path):
    p = _rules_file(tmp_path, [
        {"name": "x", "metric": "sessions", "op": ">"},
    ])
    with pytest.raises(RulesError, match="missing"):
        load_rules(p)


def test_load_rules_rejects_unknown_key(tmp_path):
    p = _rules_file(tmp_path, [
        {"name": "x", "metric": "sessions", "op": ">",
         "threshold": 1, "severity": "high"},
    ])
    with pytest.raises(RulesError, match="unknown key"):
        load_rules(p)


# -- evaluate ------------------------------------------------------------------


def test_evaluate_fires_and_skips():
    metrics = {"sessions": 4, "cost_usd_total": 0.542,
               "context_tokens_peak": None}
    rules = [
        {"name": "cap", "metric": "cost_usd_total",
         "op": ">", "threshold": 0.5},
        {"name": "burst", "metric": "sessions",
         "op": ">", "threshold": 100},
        {"name": "ctx", "metric": "context_tokens_peak",
         "op": ">", "threshold": 1},
    ]
    report = evaluate(rules, metrics)
    assert report["status"] == "fired"
    assert [a["name"] for a in report["fired"]] == ["cap"]
    assert report["fired"][0]["value"] == 0.542
    assert [s["name"] for s in report["skipped"]] == ["ctx"]


def test_evaluate_unknown_metric_is_usage_error():
    with pytest.raises(RulesError, match="unknown metric"):
        evaluate(
            [{"name": "x", "metric": "nope", "op": ">", "threshold": 1}],
            {"sessions": 4},
        )


def test_evaluate_clean():
    report = evaluate(
        [{"name": "x", "metric": "sessions", "op": "<", "threshold": 1}],
        {"sessions": 4},
    )
    assert report["status"] == "clean"
    assert report["fired"] == []


# -- cli -----------------------------------------------------------------------


def test_alerts_fires_and_exits_one(sessions_db, acp_dir, tmp_path, capsys):
    # fixture cost_usd_total is 0.542 — a 0.5 cap must fire
    rules = _rules_file(tmp_path, [
        {"name": "cost-cap", "metric": "cost_usd_total",
         "op": ">", "threshold": 0.5},
    ])
    rc = main(["alerts", "--sessions-db", str(sessions_db),
               "--acp-dir", str(acp_dir), "--rules", str(rules)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "ALERT cost-cap" in out
    assert "cost_usd_total=0.542" in out


def test_alerts_all_clear_exits_zero(sessions_db, acp_dir, tmp_path, capsys):
    rules = _rules_file(tmp_path, [
        {"name": "cost-cap", "metric": "cost_usd_total",
         "op": ">", "threshold": 5.0},
        {"name": "burst", "metric": "sessions",
         "op": ">", "threshold": 100},
    ])
    rc = main(["alerts", "--sessions-db", str(sessions_db),
               "--acp-dir", str(acp_dir), "--rules", str(rules)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "all clear" in out
    assert "ALERT" not in out


def test_alerts_skip_non_numeric_metric(sessions_db, acp_dir, tmp_path,
                                        capsys):
    # fixture nodes carry no num_tokens_preceding → context stays None
    rules = _rules_file(tmp_path, [
        {"name": "ctx", "metric": "context_tokens_peak",
         "op": ">", "threshold": 1},
    ])
    rc = main(["alerts", "--sessions-db", str(sessions_db),
               "--acp-dir", str(acp_dir), "--rules", str(rules)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SKIP" in out and "ctx" in out


def test_alerts_unknown_metric_exits_two(sessions_db, acp_dir, tmp_path,
                                         capsys):
    rules = _rules_file(tmp_path, [
        {"name": "x", "metric": "not_a_metric",
         "op": ">", "threshold": 1},
    ])
    rc = main(["alerts", "--sessions-db", str(sessions_db),
               "--acp-dir", str(acp_dir), "--rules", str(rules)])
    assert rc == 2
    assert "unknown metric" in capsys.readouterr().err


def test_alerts_missing_file_exits_two(sessions_db, acp_dir, tmp_path,
                                       capsys):
    rc = main(["alerts", "--sessions-db", str(sessions_db),
               "--acp-dir", str(acp_dir),
               "--rules", str(tmp_path / "gone.json")])
    assert rc == 2
    assert capsys.readouterr().err


def test_alerts_json_shape(sessions_db, acp_dir, tmp_path, capsys):
    rules = _rules_file(tmp_path, [
        {"name": "cost-cap", "metric": "cost_usd_total",
         "op": ">=", "threshold": 0.5},
    ])
    rc = main(["alerts", "--sessions-db", str(sessions_db),
               "--acp-dir", str(acp_dir), "--rules", str(rules), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["status"] == "fired"
    assert data["rules"] == 1
    assert data["fired"][0]["name"] == "cost-cap"
    assert data["fired"][0]["value"] == pytest.approx(0.542)
