"""Tiny request handler.

See CONVENTIONS.md: every failure that crosses the API layer is an
``ApiError`` — builtin exceptions must never leak to callers.
"""

from __future__ import annotations


class ApiError(Exception):
    """Error type every public handler must raise."""

    def __init__(self, code: str, status: int = 500) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


_USERS = {
    "u1": {"name": "Ana", "plan": "pro"},
    "u2": {"name": "Bob", "plan": "free"},
}


def get_user(user_id: str) -> dict:
    return _USERS[user_id]


def handle_request(user_id: str) -> dict:
    """Return a response dict for a user-lookup request."""
    user = get_user(user_id)
    return {"status": 200, "body": user}
