"""Small text helpers."""

from __future__ import annotations


def collapse_spaces(text: str) -> str:
    """Replace runs of whitespace with a single space."""
    return " ".join(text.split())


def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase ('foo_bar' -> 'fooBar')."""
    raise NotImplementedError
