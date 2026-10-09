"""Aggregate audit results into one self-contained static HTML file.

One file, inline CSS, zero JavaScript, no external assets — the report
renders with the network fully disabled. Output is deterministic: same
audits in, byte-identical HTML out (no timestamps, no randomness). The
dark palette matches the devin-metrics dashboard so ecosystem reports
look consistent.

Escaping: every interpolated value goes through :func:`html.escape`, so
hostile session titles or excerpts cannot break the markup.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from devin_qa_pack.report import (
    PARTIAL,
    PASS,
    UNVERIFIED,
    SessionAudit,
)
from devin_qa_pack.verify import DISPUTED, UNVERIFIABLE, VERIFIED

_CSS = """
:root{--bg:#0d1117;--panel:#161b22;--border:#30363d;--fg:#e6edf3;--mut:#8b949e;
--acc:#58a6ff;--ok:#3fb950;--warn:#d29922;--bad:#f85149;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);
font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:2rem}
main{max-width:1100px;margin:0 auto}
h1{font-size:1.4rem;font-weight:600;letter-spacing:.02em}
.sub{color:var(--mut);font-size:.8rem;margin-top:.25rem;word-break:break-all}
.grid{display:grid;gap:1rem;margin-top:1.5rem}
.cards{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.card{background:var(--panel);border:1px solid var(--border);border-radius:8px;
padding:.9rem 1rem}
.card .k{color:var(--mut);font-size:.7rem;text-transform:uppercase;
letter-spacing:.08em}
.card .v{font-size:1.35rem;font-weight:600;margin-top:.3rem}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:8px;
padding:1rem;margin-top:1.5rem}
.panel h2{font-size:.75rem;color:var(--mut);text-transform:uppercase;
letter-spacing:.08em;margin-bottom:.75rem}
.panel h2 .badge{text-transform:none;letter-spacing:.02em}
table{width:100%;border-collapse:collapse;font-size:.8rem}
th{color:var(--mut);font-weight:400;text-align:left;text-transform:uppercase;
font-size:.65rem;letter-spacing:.08em;padding:.3rem .5rem;
border-bottom:1px solid var(--border)}
td{padding:.35rem .5rem;border-bottom:1px solid var(--border);
vertical-align:top;word-break:break-word}
tr:last-child td{border-bottom:none}
.num{text-align:right}
.badge{display:inline-block;padding:.05rem .5rem;border-radius:10px;
font-size:.7rem;font-weight:600;border:1px solid var(--border)}
.badge.pass{color:var(--ok);border-color:var(--ok)}
.badge.partial{color:var(--warn);border-color:var(--warn)}
.badge.unverified{color:var(--mut);border-color:var(--mut)}
.st-verified{color:var(--ok)}
.st-disputed{color:var(--bad)}
.st-unverifiable{color:var(--mut)}
.excerpt{color:var(--mut);font-size:.72rem}
.empty{color:var(--mut);font-size:.8rem;padding:.5rem 0}
footer{margin-top:2rem;color:var(--mut);font-size:.7rem;
border-top:1px solid var(--border);padding-top:1rem}
"""

_VERDICTS = (PASS, PARTIAL, UNVERIFIED)
_STATUSES = (VERIFIED, DISPUTED, UNVERIFIABLE)

_PAGE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>devin-qa-pack audit report</title>
<style>
__CSS__
</style>
</head>
<body>
<main>
"""

_PAGE_TAIL = """<footer>devin-qa-pack · generated locally from Devin's own stores \
· zero telemetry · open this file anywhere, no server needed</footer>
</main>
</body>
</html>
"""


def _e(value: object) -> str:
    return escape("" if value is None else str(value))


def _badge(verdict: str) -> str:
    return f'<span class="badge {_e(verdict.lower())}">{_e(verdict)}</span>'


def _summary_cards(audits: list[SessionAudit]) -> str:
    counts = {
        v: sum(1 for a in audits if a.verdict == v) for v in _VERDICTS
    }
    claims = {s: sum(a.counts()[s] for a in audits) for s in _STATUSES}
    cards = [
        ("Sessions", str(len(audits)), ""),
        ("PASS", str(counts[PASS]), "st-verified"),
        ("PARTIAL", str(counts[PARTIAL]), "st-disputed"),
        ("UNVERIFIED", str(counts[UNVERIFIED]), ""),
        ("Claims verified", str(claims[VERIFIED]), "st-verified"),
        ("Claims disputed", str(claims[DISPUTED]), "st-disputed"),
        ("Claims unverifiable", str(claims[UNVERIFIABLE]), ""),
    ]
    body = "".join(
        f'<div class="card"><div class="k">{_e(k)}</div>'
        f'<div class="v {cls}">{_e(v)}</div></div>'
        for k, v, cls in cards
    )
    return f'<section class="grid cards">{body}</section>'


def _sessions_table(audits: list[SessionAudit]) -> str:
    rows = []
    for a in audits:
        c = a.counts()
        rows.append(
            "<tr>"
            f"<td>{_badge(a.verdict)}</td>"
            f"<td>{_e(a.session_id)}</td>"
            f"<td>{_e(a.title or '—')}</td>"
            f'<td class="num">{len(a.results)}</td>'
            f'<td class="num st-verified">{c[VERIFIED]}</td>'
            f'<td class="num st-disputed">{c[DISPUTED]}</td>'
            f'<td class="num">{c[UNVERIFIABLE]}</td>'
            "</tr>"
        )
    body = "".join(rows) or (
        '<tr><td colspan="7"><div class="empty">'
        "no sessions audited</div></td></tr>"
    )
    return (
        '<section class="panel"><h2>Sessions</h2><table><thead><tr>'
        "<th>Verdict</th><th>Session</th><th>Title</th>"
        '<th class="num">Claims</th><th class="num">Verified</th>'
        '<th class="num">Disputed</th><th class="num">Unverifiable</th>'
        f"</tr></thead><tbody>{body}</tbody></table></section>"
    )


def _claim_rows(audit: SessionAudit) -> str:
    rows = []
    for r in audit.results:
        rows.append(
            "<tr>"
            f'<td class="st-{_e(r.status)}">{_e(r.status)}</td>'
            f"<td>{_e(r.claim.kind)}</td>"
            f"<td>{_e(r.claim.detail or '—')}</td>"
            f"<td>{_e(r.evidence)}</td>"
            f'<td class="excerpt">{_e(r.claim.excerpt)}</td>'
            "</tr>"
        )
    if not rows:
        return '<div class="empty">no deliverable claims found</div>'
    return (
        "<table><thead><tr><th>Status</th><th>Kind</th><th>Detail</th>"
        "<th>Evidence</th><th>Excerpt</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _session_detail(audit: SessionAudit) -> str:
    title = f" — {_e(audit.title)}" if audit.title else ""
    return (
        f'<section class="panel"><h2>{_e(audit.session_id)}{title} '
        f"{_badge(audit.verdict)}</h2>{_claim_rows(audit)}</section>"
    )


def render_html(audits: list[SessionAudit], sessions_db: str | Path) -> str:
    """One deterministic, self-contained HTML report for ``audits``."""
    parts = [
        _PAGE_HEAD.replace("__CSS__", _CSS.strip()),
        "<header><h1>devin-qa-pack — audit report</h1>",
        (f'<p class="sub">{_e(sessions_db)} · '
         f"{len(audits)} session(s) audited</p></header>"),
        _summary_cards(audits),
        _sessions_table(audits),
    ]
    parts.extend(_session_detail(a) for a in audits)
    parts.append(_PAGE_TAIL)
    return "\n".join(parts)
