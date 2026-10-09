"""Order pricing.

See CONVENTIONS.md: all money math rounds through ``_round_cents`` —
the built-in ``round()`` is never used on money.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def _round_cents(amount: float) -> float:
    """Round to cents, half-up (matches invoice rounding)."""
    cents = Decimal(str(amount)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(cents)


def unit_price(base: float) -> float:
    return _round_cents(base)


def line_price(base: float, qty: int) -> float:
    return _round_cents(base * qty)


def total_with_tax(base: float, tax_rate: float) -> float:
    return _round_cents(base * (1 + tax_rate))
