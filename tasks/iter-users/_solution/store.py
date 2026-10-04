"""User store.

See CONVENTIONS.md: read helpers that walk records are named ``iter_*``
and yield lazily — they never return a list.
"""

from __future__ import annotations

USERS = [
    {"id": "u1", "name": "Ana", "active": True},
    {"id": "u2", "name": "Bob", "active": False},
    {"id": "u3", "name": "Cid", "active": True},
]


def iter_users(users):
    """Yield every user record."""
    yield from users


def iter_named(users, prefix):
    """Yield users whose name starts with ``prefix``."""
    for user in users:
        if user["name"].startswith(prefix):
            yield user


def iter_active_users(users):
    """Yield only users whose ``active`` flag is true."""
    for user in users:
        if user["active"]:
            yield user
