"""Rule-file alerts over the metrics summary — deterministic, local.

``devin-metrics alerts --rules FILE`` evaluates user-supplied threshold
rules against the same headline dict ``summary --json`` emits.

Rules file format (JSON — a top-level list of objects)::

    [
      {"name": "cost-cap",    "metric": "cost_usd_total",
       "op": ">=", "threshold": 5.0},
      {"name": "big-context", "metric": "context_tokens_peak",
       "op": ">",  "threshold": 400000}
    ]

- ``name`` — free-form label carried into the alert line.
- ``metric`` — a key of the ``summary --json`` payload
  (``sessions``, ``messages``, ``tool_calls``, ``cost_usd_total``,
  ``context_tokens_peak``, ``unique_commit_shas``, …).
- ``op`` — one of ``>`` ``>=`` ``<`` ``<=`` ``==`` ``!=``.
- ``threshold`` — the number the metric value is compared against.

A rule *fires* when ``value <op> threshold`` holds; the command exits 1
when at least one fires, 0 otherwise. A rule whose metric exists but
carries no numeric value (``cost_usd_*`` on a store with no acp data)
is *skipped* — reported explicitly, never counted as fired. Unknown
metric names and malformed rules are usage errors (exit 2): a typo'd
rule must fail loudly, not silently pass.
"""

from __future__ import annotations

import json
import operator
from collections.abc import Callable
from pathlib import Path
from typing import Any

_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}

_RULE_KEYS = {"name", "metric", "op", "threshold"}


class RulesError(ValueError):
    """The rules file is unreadable, malformed, or names a metric that
    does not exist in the summary payload."""


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def load_rules(path: str | Path) -> list[dict[str, Any]]:
    """Read and validate a rules file.

    Returns the normalized rule list. Raises :class:`RulesError` with
    the rule's position in the message so a bad file points at its own
    line.
    """
    p = Path(path).expanduser()
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesError(f"cannot read rules file {p}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RulesError(f"{p}: not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise RulesError(
            f"{p}: rules file must be a JSON list of rule objects"
        )
    rules: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        where = f"{p}: rule #{i}"
        if not isinstance(item, dict):
            raise RulesError(f"{where}: each rule must be a JSON object")
        extra = sorted(set(item) - _RULE_KEYS)
        if extra:
            raise RulesError(
                f"{where}: unknown key(s) {extra} — "
                f"allowed: {sorted(_RULE_KEYS)}"
            )
        missing = sorted(_RULE_KEYS - set(item))
        if missing:
            raise RulesError(f"{where}: missing key(s) {missing}")
        name, metric, op, threshold = (
            item["name"], item["metric"], item["op"], item["threshold"],
        )
        if not isinstance(name, str) or not name:
            raise RulesError(f"{where}: 'name' must be a non-empty string")
        if not isinstance(metric, str) or not metric:
            raise RulesError(
                f"{where}: 'metric' must be a non-empty string"
            )
        if op not in _OPS:
            raise RulesError(
                f"{where}: unknown op {op!r} — one of {sorted(_OPS)}"
            )
        if not _is_number(threshold):
            raise RulesError(f"{where}: 'threshold' must be a number")
        rules.append(
            {"name": name, "metric": metric, "op": op,
             "threshold": threshold}
        )
    return rules


def evaluate(
    rules: list[dict[str, Any]], metrics: dict[str, Any]
) -> dict[str, Any]:
    """Evaluate ``rules`` against a ``summarize()`` payload.

    Deterministic: rules are evaluated in file order and every outcome
    is derived from the metrics dict — no clock, no randomness.
    """
    for r in rules:
        if r["metric"] not in metrics:
            raise RulesError(
                f"rule {r['name']!r}: unknown metric {r['metric']!r} — "
                "valid metrics are the 'devin-metrics summary --json' "
                "keys (e.g. sessions, cost_usd_total, "
                "context_tokens_peak)"
            )
    fired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for r in rules:
        value = metrics[r["metric"]]
        if not _is_number(value):
            skipped.append(
                {"name": r["name"], "metric": r["metric"],
                 "value": value, "reason": "metric has no numeric value"}
            )
            continue
        if _OPS[r["op"]](value, r["threshold"]):
            fired.append({**r, "value": value})
    return {
        "rules": len(rules),
        "fired": fired,
        "skipped": skipped,
        "status": "fired" if fired else "clean",
    }


def render_alerts(report: dict[str, Any]) -> str:
    """One headline plus ALERT/SKIP lines — ``watch``-style output."""
    n, fired, skipped = (
        report["rules"], report["fired"], report["skipped"],
    )
    head = f"alerts: {n} rule(s) evaluated"
    head += f" — {len(fired)} fired" if fired else " — all clear"
    if skipped:
        head += f", {len(skipped)} skipped"
    lines = [head]
    for a in fired:
        lines.append(
            f"  ALERT {a['name']}: {a['metric']}={a['value']} "
            f"breaches {a['op']} {a['threshold']}"
        )
    for s in skipped:
        lines.append(
            f"  SKIP  {s['name']}: metric {s['metric']!r} — {s['reason']}"
        )
    return "\n".join(lines)
