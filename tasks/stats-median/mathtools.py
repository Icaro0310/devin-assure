"""Small statistics helpers."""

from __future__ import annotations


def add(a: float, b: float) -> float:
    return a + b


def mean(values: list) -> float:
    if not values:
        raise ValueError("mean of empty sequence")
    return sum(values) / len(values)


def median(values: list) -> float:
    if not values:
        raise ValueError("median of empty sequence")
    ordered = sorted(values)
    return ordered[len(ordered) // 2]
