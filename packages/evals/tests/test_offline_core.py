"""Offline-core guard: the core must not open sockets.

Release checklist item: "no-network core test (CI): fails if the core
opens a socket". An autouse fixture monkeypatches ``socket.socket.connect``,
``socket.socket.connect_ex`` and ``socket.create_connection`` to raise
``OfflineCoreError`` for the duration of every test in this file, then the
tests run the repo's core operations end to end. This is a guard, not a
mock: any in-process network access fails the suite.

Intentional online paths are excluded by design: the ``judge`` and
``ab-run`` commands drive the Devin bridge as a subprocess and are opt-in —
an in-process socket block cannot reach subprocess sockets anyway. Offline
replay (``run`` / ``run_evals``) is the core under test here.

Opt-out: mark a test ``@pytest.mark.network`` to run it without the socket
block (reserved for tests that intentionally exercise the network).

Run with:
``PYTHONPATH=src:../devin-internals-spec/src python -m pytest tests/test_offline_core.py``
(this repo imports ``devin_internals`` from the sibling checkout).
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from devin_evals.runner import run_evals

from conftest import SESSION_TITLE, write_eval


class OfflineCoreError(RuntimeError):
    """Raised when core code tries to open a network connection."""


REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "corpus"


def _offline_fail(*args, **kwargs):
    raise OfflineCoreError("core opened a socket during the offline-core test")


@pytest.fixture(autouse=True)
def _block_sockets(request, monkeypatch):
    """Block all outbound sockets; opt out with ``@pytest.mark.network``."""
    if request.node.get_closest_marker("network"):
        return
    monkeypatch.setattr(socket.socket, "connect", _offline_fail)
    monkeypatch.setattr(socket.socket, "connect_ex", _offline_fail)
    monkeypatch.setattr(socket, "create_connection", _offline_fail)


def test_socket_block_is_active():
    """Sanity check: the guard itself raises on any connect attempt."""
    with pytest.raises(OfflineCoreError):
        socket.create_connection(("127.0.0.1", 1), timeout=0.01)
    with pytest.raises(OfflineCoreError):
        socket.socket().connect(("127.0.0.1", 1))


def test_run_evals_on_conftest_fixture_offline(evals_dir, sessions_db,
                                               tmp_path):
    """Replay a passing eval case against the conftest sessions.db."""
    write_eval(evals_dir, "a", {
        "id": "ok",
        "description": "passes on the fixture session",
        "session_ref": SESSION_TITLE,
        "rubric": [
            {"grader": "contains", "text": "all tests pass"},
            {"grader": "tool_called", "name": "run_shell",
             "args_substr": "pytest"},
            {"grader": "exit_code", "value": 0, "mode": "all"},
            {"grader": "no_secrets"},
        ],
    })

    report = run_evals(evals_dir, sessions_db, out_dir=tmp_path / "out")

    (case,) = report["cases"]
    assert case["status"] == "pass"
    assert report["summary"]["score"] == 1.0
    assert (tmp_path / "out" / "report.json").is_file()


def test_run_evals_on_committed_corpus_offline():
    """The committed synthetic corpus must grade offline, verdicts as
    recorded in ``corpus/corpus.json``."""
    manifest = json.loads((CORPUS / "corpus.json").read_text())
    report = run_evals(CORPUS / "evals", CORPUS / "sessions.db")

    actual = {c["id"]: c["status"] for c in report["cases"]}
    assert len(actual) == len(manifest["cases"])
    # Cases flagged with a documented ``known_gap`` are allowed to deviate
    # from ``expected_status`` — the gap is the point of the corpus entry.
    for c in manifest["cases"]:
        if not c.get("known_gap"):
            assert actual[c["id"]] == c["expected_status"], c["id"]
