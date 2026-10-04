"""Sales report renderer.

See CONVENTIONS.md: private formatting helpers live in ``_fmt.py``;
public modules import them with ``from _fmt import ...``.
"""

from __future__ import annotations


def _money(amount):
    return f"${amount:,.2f}"


def _pct(ratio):
    return f"{ratio * 100:.1f}%"


def _row(label, value):
    return f"{label:<12} {value}"


def render_report(sales):
    """Render a plain-text summary of a list of sale amounts."""
    total = sum(sales)
    avg = total / len(sales) if sales else 0.0
    top = max(sales) if sales else 0.0
    share = top / total if total else 0.0
    lines = [
        _row("total", _money(total)),
        _row("average", _money(avg)),
        _row("top", _money(top)),
        _row("top share", _pct(share)),
        _row("count", str(len(sales))),
    ]
    return "\n".join(lines)
