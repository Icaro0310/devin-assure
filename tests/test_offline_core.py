"""Offline-core guard: the core must not open sockets.

Release checklist item: "no-network core test (CI): fails if the core
opens a socket". An autouse fixture monkeypatches ``socket.socket.connect``,
``socket.socket.connect_ex`` and ``socket.create_connection`` to raise
``OfflineCoreError`` for the duration of every test in this file, then the
tests run the repo's core operations end to end. This is a guard, not a
mock: any in-process network access fails the suite.

Intentional online paths are excluded by design: the summary/projects/
daily/dashboard aggregations are read-only on local SQLite stores. The
``watch`` loop is a live poller but still local-only; report fetchers that
hit remote services in sibling repos spawn subprocesses and are out of
scope for an in-process socket block anyway.

Opt-out: mark a test ``@pytest.mark.network`` to run it without the socket
block (reserved for tests that intentionally exercise the network).

Run with:
``PYTHONPATH=src:../devin-internals-spec/src python -m pytest tests/test_offline_core.py``
(this repo imports ``devin_internals`` from the sibling checkout).
"""

from __future__ import annotations

import json
import socket

import pytest

from devin_metrics.cli import main


class OfflineCoreError(RuntimeError):
    """Raised when core code tries to open a network connection."""


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


def test_summary_on_synthetic_data_dir_offline(data_dir, capsys):
    rc = main(["summary", "--data-dir", str(data_dir), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["sessions"] == 4
    assert data["cost_usd_total"] == pytest.approx(0.542)


def test_projects_and_daily_offline(sessions_db, acp_dir, capsys):
    args = ["--sessions-db", str(sessions_db), "--acp-dir", str(acp_dir)]

    assert main(["projects", *args, "--json"]) == 0
    projects = json.loads(capsys.readouterr().out)
    assert len(projects) >= 1

    assert main(["daily", *args, "--json"]) == 0
    assert capsys.readouterr().out.strip()
