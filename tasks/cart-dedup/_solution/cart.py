"""Shopping-cart totals."""

from __future__ import annotations


def _line_total(item: dict) -> float:
    return item["price"] * item["qty"]


def subtotal(items: list) -> float:
    total = 0.0
    for item in items:
        total += _line_total(item)
    return total


def total_with_discount(items: list, discount: float) -> float:
    total = 0.0
    for item in items:
        total += _line_total(item)
    return total * (1 - discount)


def total_with_tax(items: list, tax_rate: float) -> float:
    total = 0.0
    for item in items:
        total += _line_total(item)
    return total * (1 + tax_rate)
