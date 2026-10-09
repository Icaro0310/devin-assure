"""Private formatting helpers for report modules."""

from __future__ import annotations


def _money(amount):
    return f"${amount:,.2f}"


def _pct(ratio):
    return f"{ratio * 100:.1f}%"


def _row(label, value):
    return f"{label:<12} {value}"
