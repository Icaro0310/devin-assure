"""Tests for the optional task-ledger consumer surface."""

from __future__ import annotations

import builtins
import json
import sys
import types

from devin_qa_pack import ledger_audit


def test_run_without_devin_memory_reports_install_hint():
    """No devin-memory installed → clear error dict, not an exception."""
    # Force the ImportError path regardless of the ambient environment.
    real_import = __import__

    def _blocked(name, *a, **k):
        if name.startswith("devin_memory"):
            raise ImportError("blocked for test")
        return real_import(name, *a, **k)

    builtins.__import__ = _blocked
    try:
        rep = ledger_audit.run()
    finally:
        builtins.__import__ = real_import
    assert rep["error"] == "devin-memory not installed"
    assert "devin-qa-pack[ledger]" in rep["install"]


def test_run_with_fake_ledger_module(tmp_path, monkeypatch):
    """Happy path: a stub devin_memory.ledger.integrations.audit is used."""
    seen = {}

    def fake_audit(ledger, parked_days=7):
        seen["parked_days"] = parked_days
        seen["dir"] = str(ledger.dir)
        return {"counts": {"queued": 1, "active": 0, "parked": 0, "blocked": 0},
                "total": 1, "parked_overdue": [], "blocked": [],
                "active": [{"id": "DL-x", "title": "t", "status": "active",
                            "updated": ""}],
                "parked_days_threshold": parked_days}

    pkg = types.ModuleType("devin_memory")
    sub = types.ModuleType("devin_memory.ledger")
    integ = types.ModuleType("devin_memory.ledger.integrations")

    class _L:
        def __init__(self, path=None):
            self.dir = path

    sub.Ledger = _L
    integ.audit = fake_audit
    pkg.ledger = sub
    monkeypatch.setitem(sys.modules, "devin_memory", pkg)
    monkeypatch.setitem(sys.modules, "devin_memory.ledger", sub)
    monkeypatch.setitem(sys.modules, "devin_memory.ledger.integrations", integ)

    rep = ledger_audit.run(parked_days=3, ledger_dir=str(tmp_path))
    assert rep["total"] == 1
    assert seen["parked_days"] == 3
    assert seen["dir"] == str(tmp_path)


def test_main_missing_dep_exits_zero(capsys, monkeypatch):
    """CLI contract: missing optional dep → exit 0 with install hint."""
    monkeypatch.setattr(ledger_audit, "run",
                        lambda *a, **k: {"error": "devin-memory not installed",
                                         "install": "pip install x"})
    assert ledger_audit.main([]) == 0
    out = capsys.readouterr().out
    assert "devin-memory not installed" in out
    assert "pip install" in out


def test_main_json_shape(capsys, monkeypatch):
    monkeypatch.setattr(
        ledger_audit, "run",
        lambda *a, **k: {"counts": {"queued": 0, "active": 1, "parked": 0,
                                   "blocked": 0},
                         "total": 1, "parked_overdue": [], "blocked": [],
                         "active": [], "parked_days_threshold": 7})
    assert ledger_audit.main(["--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["counts"]["active"] == 1
