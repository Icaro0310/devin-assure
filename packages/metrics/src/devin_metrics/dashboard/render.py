"""Render the stats dict as a single self-contained ``index.html``.

Jinja-free string template: one HTML file with the stats dict embedded as an
``application/json`` block plus vanilla JS that draws inline SVG bar/line
charts. No CDN, no fonts, no external assets — the output renders with the
network fully disabled (the only ``http://`` string is the SVG namespace).

Chart targets are fixed ids (``chart-daily-sessions``, ``chart-daily-cost``,
``chart-projects``, ``chart-models``) so tests can assert on the skeleton
without executing JS.
"""

from __future__ import annotations

import json
from typing import Any

_CSS = """
:root{--bg:#0d1117;--panel:#161b22;--border:#30363d;--fg:#e6edf3;--mut:#8b949e;
--acc:#58a6ff;--acc2:#3fb950;--warn:#d29922;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);
font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:2rem}
main{max-width:1100px;margin:0 auto}
h1{font-size:1.4rem;font-weight:600;letter-spacing:.02em}
.sub{color:var(--mut);font-size:.8rem;margin-top:.25rem}
.grid{display:grid;gap:1rem;margin-top:1.5rem}
.cards{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.card{background:var(--panel);border:1px solid var(--border);border-radius:8px;
padding:.9rem 1rem}
.card .k{color:var(--mut);font-size:.7rem;text-transform:uppercase;
letter-spacing:.08em}
.card .v{font-size:1.35rem;font-weight:600;margin-top:.3rem}
.charts{grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:8px;
padding:1rem}
.panel h2{font-size:.75rem;color:var(--mut);text-transform:uppercase;
letter-spacing:.08em;margin-bottom:.75rem}
svg{display:block;width:100%;height:auto}
.bar{fill:var(--acc)}
.bar.alt{fill:var(--acc2)}
.line{fill:none;stroke:var(--acc2);stroke-width:2}
.dot{fill:var(--acc2)}
.axis{stroke:var(--border);stroke-width:1}
.lbl{fill:var(--mut);font-size:9px;font-family:inherit}
table{width:100%;border-collapse:collapse;font-size:.8rem}
th{color:var(--mut);font-weight:400;text-align:left;text-transform:uppercase;
font-size:.65rem;letter-spacing:.08em;padding:.3rem .5rem;
border-bottom:1px solid var(--border)}
td{padding:.35rem .5rem;border-bottom:1px solid var(--border)}
tr:last-child td{border-bottom:none}
.num{text-align:right}
.note{color:var(--mut);font-size:.75rem;padding:.5rem 0}
footer{margin-top:2rem;color:var(--mut);font-size:.7rem;
border-top:1px solid var(--border);padding-top:1rem}
"""

_JS = r"""
const DATA = JSON.parse(document.getElementById("stats").textContent);
const NS = "http://www.w3.org/2000/svg";
const fmtInt = v => v == null ? "—" : Math.round(v).toLocaleString("en-US");
const fmtUsd = v => v == null ? "—" : "$" + v.toFixed(4);
const fmtDur = ms => {
  if (ms == null) return "—";
  const s = Math.floor(ms / 1000);
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m " + String(s % 60).padStart(2, "0") + "s";
  return Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m";
};
const short = p => p == null ? "—" : (p.length > 26 ? "…" + p.slice(-25) : p);

function el(tag, attrs, parent, text) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  parent.appendChild(e);
  return e;
}

function svg(host, h) {
  const s = el("svg", {viewBox: "0 0 640 " + h, role: "img"}, host);
  return s;
}

// -- vertical bar chart (daily sessions) ------------------------------------
function barChart(id, rows, getVal, fmt) {
  const host = document.getElementById(id);
  host.textContent = "";
  const W = 640, H = 200, padL = 44, padB = 34, padT = 8;
  const s = svg(host, H);
  const max = Math.max(1, ...rows.map(getVal));
  const bw = rows.length ? (W - padL - 8) / rows.length : W;
  const bh = H - padB - padT;
  el("line", {x1: padL, y1: H - padB, x2: W - 4, y2: H - padB, "class": "axis"}, s);
  el("line", {x1: padL, y1: padT, x2: padL, y2: H - padB, "class": "axis"}, s);
  el("text", {x: 4, y: padT + 8, "class": "lbl"}, s, fmt(max));
  rows.forEach((r, i) => {
    const v = getVal(r);
    const h = Math.max(1, (v / max) * bh);
    const x = padL + i * bw + bw * 0.15;
    el("rect", {x, y: H - padB - h, width: bw * 0.7, height: h,
                "class": "bar"}, s);
    const t = el("text", {x: x + bw * 0.35, y: H - padB + 12,
                          "text-anchor": "middle", "class": "lbl"}, s,
                 r.date.slice(5));
    t.setAttribute("transform",
      "rotate(-30 " + (x + bw * 0.35) + " " + (H - padB + 12) + ")");
    el("text", {x: x + bw * 0.35, y: H - padB - h - 4,
                "text-anchor": "middle", "class": "lbl"}, s, fmt(v));
  });
}

// -- line chart (daily cost) -------------------------------------------------
function lineChart(id, rows, getVal, fmt) {
  const host = document.getElementById(id);
  host.textContent = "";
  const pts = rows.map((r, i) => ({r, i, v: getVal(r)})).filter(p => p.v != null);
  if (!pts.length) {
    const d = document.createElement("div");
    d.className = "note";
    d.textContent = "no cost data — acp-messages store missing or empty";
    host.appendChild(d);
    return;
  }
  const W = 640, H = 200, padL = 52, padB = 34, padT = 8;
  const s = svg(host, H);
  const max = Math.max(...pts.map(p => p.v)) || 1;
  const n = Math.max(1, rows.length - 1);
  const X = i => padL + (i / n) * (W - padL - 12);
  const Y = v => padT + (1 - v / max) * (H - padB - padT);
  el("line", {x1: padL, y1: H - padB, x2: W - 4, y2: H - padB, "class": "axis"}, s);
  el("line", {x1: padL, y1: padT, x2: padL, y2: H - padB, "class": "axis"}, s);
  el("text", {x: 4, y: padT + 8, "class": "lbl"}, s, fmt(max));
  el("path", {d: pts.map((p, k) =>
      (k ? "L" : "M") + X(p.i) + " " + Y(p.v)).join(" "), "class": "line"}, s);
  pts.forEach(p => {
    el("circle", {cx: X(p.i), cy: Y(p.v), r: 3, "class": "dot"}, s);
    const t = el("text", {x: X(p.i), y: H - padB + 12, "text-anchor": "middle",
                          "class": "lbl"}, s, p.r.date.slice(5));
    t.setAttribute("transform",
      "rotate(-30 " + X(p.i) + " " + (H - padB + 12) + ")");
  });
}

// -- horizontal bars (projects / models) -------------------------------------
function hbarChart(id, rows, labelKey, valKey, altKey, fmt) {
  const host = document.getElementById(id);
  host.textContent = "";
  const useAlt = rows.every(r => r[valKey] == null) && altKey != null;
  const get = r => useAlt ? (r[altKey] || 0) : (r[valKey] || 0);
  const rowH = 26, H = Math.max(60, rows.length * rowH + 8);
  const s = svg(host, H);
  const max = Math.max(1, ...rows.map(get));
  const labW = 150, W = 640;
  rows.forEach((r, i) => {
    const y = i * rowH + 4;
    const v = get(r);
    const w = Math.max(2, (v / max) * (W - labW - 90));
    el("text", {x: labW - 6, y: y + rowH / 2, "text-anchor": "end",
                "class": "lbl"}, s, short(r[labelKey]));
    el("rect", {x: labW, y: y + 3, width: w, height: rowH - 8,
                "class": useAlt ? "bar" : "bar alt"}, s);
    el("text", {x: labW + w + 6, y: y + rowH / 2, "class": "lbl"}, s,
       (useAlt ? fmtInt : fmt)(v));
  });
  if (useAlt) {
    const d = document.createElement("div");
    d.className = "note";
    d.textContent = "showing session counts — cost data unavailable";
    host.appendChild(d);
  }
}

// -- tables -------------------------------------------------------------------
function sessionTable(tbodyId, rows) {
  const tb = document.getElementById(tbodyId);
  rows.forEach(s => {
    const tr = document.createElement("tr");
    [s.title || s.id.slice(0, 8), short(s.project), fmtDur(s.duration_ms),
     fmtUsd(s.cost_usd)].forEach((c, i) => {
      const td = document.createElement("td");
      td.textContent = c;
      if (i >= 2) td.className = "num";
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
}

// -- boot ---------------------------------------------------------------------
const S = DATA.summary, SRC = DATA.source;
const cards = [
  ["Sessions", fmtInt(S.sessions)],
  ["With cost data", fmtInt(S.sessions_with_cost)],
  ["Messages", fmtInt(S.messages)],
  ["Tool calls", fmtInt(S.tool_calls)],
  ["Total duration", fmtDur(S.duration_ms_total)],
  ["Cost (all usage)", fmtUsd(S.cost_usd_total)],
  ["Cost (sessions)", fmtUsd(S.cost_usd_by_sessions)],
  ["Input tokens", fmtInt(S.input_tokens_total)],
  ["Output tokens", fmtInt(S.output_tokens_total)],
  ["Avg cost/session", fmtUsd(S.avg_cost_usd)],
  ["Peak context tokens", fmtInt(S.peak_context_tokens)],
  ["First activity", S.first_seen || "—"],
  ["Last activity", S.last_seen || "—"],
];
const cg = document.getElementById("cards");
cards.forEach(([k, v]) => {
  const c = document.createElement("div");
  c.className = "card";
  const kk = document.createElement("div"); kk.className = "k"; kk.textContent = k;
  const vv = document.createElement("div"); vv.className = "v"; vv.textContent = v;
  c.appendChild(kk); c.appendChild(vv); cg.appendChild(c);
});
document.getElementById("src-meta").textContent =
  "sessions.db schema v" + SRC.schema_version + " · acp-messages: " +
  (SRC.acp_available
    ? SRC.acp_db_files + " db(s), " + SRC.matched_sessions + " matched, " +
      SRC.orphan_dbs + " orphan, " + SRC.acp_db_errors + " unreadable"
    : "not found");
barChart("chart-daily-sessions", DATA.daily, r => r.sessions, fmtInt);
lineChart("chart-daily-cost", DATA.daily, r => r.cost_usd, fmtUsd);
lineChart("chart-daily-context", DATA.daily, r => r.context_tokens, fmtInt);
hbarChart("chart-projects", DATA.projects, "project", "cost_usd", "sessions",
          fmtUsd);
hbarChart("chart-models", DATA.models, "model", "cost_usd", "sessions", fmtUsd);
sessionTable("top-longest-body", DATA.top_sessions.longest);
sessionTable("top-costliest-body", DATA.top_sessions.costliest);
"""

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>devin-dashboard</title>
<style>__CSS__</style>
</head>
<body>
<main>
<header>
<h1>devin-dashboard</h1>
<p class="sub">local usage report · <span id="src-meta"></span></p>
</header>
<section class="grid cards" id="cards"></section>
<section class="grid charts">
<div class="panel"><h2>Sessions per day</h2><div id="chart-daily-sessions"></div></div>
<div class="panel"><h2>Cost per day (USD)</h2><div id="chart-daily-cost"></div></div>
<div class="panel"><h2>Peak context tokens per day</h2><div id="chart-daily-context"></div></div>
<div class="panel"><h2>Cost by project</h2><div id="chart-projects"></div></div>
<div class="panel"><h2>Cost by model</h2><div id="chart-models"></div></div>
</section>
<section class="grid charts">
<div class="panel"><h2>Longest sessions</h2>
<table><thead><tr><th>Session</th><th>Project</th>
<th class="num">Duration</th><th class="num">Cost</th></tr></thead>
<tbody id="top-longest-body"></tbody></table></div>
<div class="panel"><h2>Costliest sessions</h2>
<table><thead><tr><th>Session</th><th>Project</th>
<th class="num">Duration</th><th class="num">Cost</th></tr></thead>
<tbody id="top-costliest-body"></tbody></table></div>
</section>
<footer>devin-dashboard v__VERSION__ · generated locally from Devin's own
stores · zero telemetry · open this file anywhere, no server needed</footer>
</main>
<script type="application/json" id="stats">__DATA__</script>
<script>__JS__</script>
</body>
</html>
"""


def render_html(stats: dict[str, Any]) -> str:
    """Render ``stats`` (from :func:`collect.collect_stats`) as one HTML file.

    The stats dict is embedded verbatim as an ``application/json`` block;
    ``</`` is escaped to ``<\\/`` so a ``</script>`` inside a session title
    cannot break out of the data block.
    """
    payload = json.dumps(stats, ensure_ascii=False).replace("</", "<\\/")
    return (
        _PAGE.replace("__CSS__", _CSS)
        .replace("__JS__", _JS)
        .replace("__DATA__", payload)
        .replace("__VERSION__", str(stats.get("version", "?")))
    )
