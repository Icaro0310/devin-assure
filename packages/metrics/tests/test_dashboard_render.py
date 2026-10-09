"""render: single-file HTML contract — embedded data, zero external assets."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from devin_metrics.dashboard.render import render_html


class _ExternalAssets(HTMLParser):
    """Collects tags that would pull external resources."""

    def __init__(self) -> None:
        super().__init__()
        self.violations: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = {name for name, _ in attrs}
        if (tag == "script" and "src" in names) or (
            tag in {"link", "img"} and {"href", "src"} & names
        ):
            self.violations.append(tag)


def test_is_single_file_html(stats):
    html = render_html(stats)
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "<style" in html and "<script" in html
    assert html.rstrip().endswith("</html>")


def test_embeds_stats_as_json(stats):
    html = render_html(stats)
    m = re.search(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert m, "stats JSON block not found"
    embedded = json.loads(m.group(1))
    assert embedded["summary"]["sessions"] == 4
    assert len(embedded["daily"]) == 3
    assert embedded["projects"][0]["project"] in {"/work/alpha", "/work/beta"}


def test_no_external_urls(stats):
    """No CDN, no fonts, no telemetry — xmlns is the only allowed http://."""
    html = render_html(stats)
    stripped = html.replace("http://www.w3.org/2000/svg", "")
    assert "http://" not in stripped
    assert "https://" not in stripped


def test_no_script_src_or_link_href(stats):
    parser = _ExternalAssets()
    parser.feed(render_html(stats))
    assert parser.violations == []  # no scripts/stylesheets/images from outside


def test_charts_rendered_by_vanilla_js(stats):
    html = render_html(stats)
    # chart targets exist; drawing happens in JS against the embedded JSON
    assert 'id="chart-daily-sessions"' in html
    assert 'id="chart-daily-cost"' in html
    assert 'id="chart-projects"' in html
    assert 'id="chart-models"' in html
    assert "getElementById" in html


def test_script_breakout_escaped(stats):
    """A '</script>' inside stats values must not break the JSON block."""
    evil = dict(stats)
    evil["summary"] = dict(stats["summary"])
    evil["top_sessions"] = {
        "longest": [{"id": "x", "title": "</script><img src=x>", "project": "p",
                     "model": "m", "created": "d", "created_at": 0,
                     "duration_ms": 0, "messages": 0, "tool_calls": 0,
                     "cost_usd": None, "input_tokens": None,
                     "output_tokens": None}],
        "costliest": [],
    }
    html = render_html(evil)
    assert "</script><img" not in html
