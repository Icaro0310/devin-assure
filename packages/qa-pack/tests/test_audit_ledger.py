"""Tests for the semantic done-claim audit of the task ledger.

No real ledger, git history, network or model is touched: ``devin_memory``
is stubbed via ``sys.modules`` (same pattern as ``test_ledger_audit.py``),
the judge client is a scripted fake injected through
``audit_ledger._build_judge``/``_noul``, and the only real subprocess is
``git`` inside the ``git_repo`` tmp fixture.
"""

from __future__ import annotations

import builtins
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
from devin_qa_pack import audit_ledger
from devin_qa_pack.cli import main as cli_main


def _task(
    tid: str = "DL-ab12",
    title: str = "Ship the thing",
    status: str = "done",
    accept: list[str] | None = None,
    evidence: str = "",
    met: list[int] | None = None,
    waived: str = "",
    waived_by: str = "",
    updated: str = "2026-11-16T00:00:00Z",
) -> dict:
    return {
        "id": tid,
        "title": title,
        "status": status,
        "accept": accept if accept is not None else ["tests pass"],
        "evidence": evidence,
        "met": met if met is not None else [1],
        "waived": waived,
        "waived_by": waived_by,
        "created": "2026-11-15T00:00:00Z",
        "updated": updated,
    }


def _stub_ledger(monkeypatch: pytest.MonkeyPatch, tasks: dict[str, dict]):
    """Install a fake ``devin_memory.ledger`` whose ``Ledger.tasks()``
    returns ``tasks`` — the same sys.modules stub test_ledger_audit uses."""
    pkg = types.ModuleType("devin_memory")
    sub = types.ModuleType("devin_memory.ledger")

    class _L:
        def __init__(self, path=None):
            self.dir = Path(path) if path else Path("/nonexistent-ledger")

        def tasks(self):
            return tasks

    sub.Ledger = _L
    pkg.ledger = sub
    monkeypatch.setitem(sys.modules, "devin_memory", pkg)
    monkeypatch.setitem(sys.modules, "devin_memory.ledger", sub)


def _no_judge(monkeypatch: pytest.MonkeyPatch):
    """Force the deterministic path regardless of the ambient env."""
    monkeypatch.setattr(audit_ledger, "_build_judge", lambda: (None, None, None))


class _Ans:
    """NoulAnswer-shaped test double."""

    def __init__(self, value, prob, abstained=False):
        self.value = value
        self.prob = prob
        self.confidence = max(prob, 1.0 - prob) if isinstance(prob, float) else 0.5
        self.abstained = abstained


class _FakeClient:
    """Scripted ``client.ask(state, questions)`` — answers keyed by
    question name; missing names default to a confident yes."""

    def __init__(self, script=None, exc=None):
        self.script = script or {}
        self.exc = exc
        self.calls = []

    def ask(self, state, questions):
        self.calls.append((state, dict(questions)))
        if self.exc is not None:
            raise self.exc
        return {
            name: self.script.get(name, _Ans(True, 0.9))
            for name in questions
        }


def _fake_judge(monkeypatch: pytest.MonkeyPatch, client):
    monkeypatch.setattr(
        audit_ledger, "_build_judge", lambda: (client, "FakeBackend", None)
    )
    # The real _noul imports poordjaevin; tests never need it installed.
    monkeypatch.setattr(audit_ledger, "_noul", lambda stmt: ("noul", stmt))


# ---------------------------------------------------------------------------
# dependency + usage errors
# ---------------------------------------------------------------------------


def test_run_without_devin_memory_reports_install_hint():
    real_import = __import__

    def _blocked(name, *a, **k):
        if name.startswith("devin_memory"):
            raise ImportError("blocked for test")
        return real_import(name, *a, **k)

    builtins.__import__ = _blocked
    try:
        rep = audit_ledger.run()
    finally:
        builtins.__import__ = real_import
    assert rep["error"] == "devin-memory not installed"
    assert "devin-qa-pack[ledger]" in rep["install"]
    assert audit_ledger.exit_code(rep) == 0


def test_main_missing_dep_exits_zero(capsys, monkeypatch):
    monkeypatch.setattr(
        audit_ledger,
        "run",
        lambda *a, **k: {"error": "devin-memory not installed",
                         "install": "pip install x"},
    )
    assert audit_ledger.main([]) == 0
    out = capsys.readouterr().out
    assert "devin-memory not installed" in out
    assert "pip install" in out


def test_no_done_tasks_is_usage_error(monkeypatch):
    _stub_ledger(monkeypatch, {"DL-1a2b": _task(status="queued")})
    rep = audit_ledger.run(judge=False)
    assert rep["usage_error"] is True
    assert "no done tasks" in rep["error"]
    assert audit_ledger.exit_code(rep) == 2


def test_task_filter_unknown_and_not_done(monkeypatch):
    tasks = {
        "DL-aaaa": _task(tid="DL-aaaa", status="done"),
        "DL-bbbb": _task(tid="DL-bbbb", status="active"),
    }
    _stub_ledger(monkeypatch, tasks)
    rep = audit_ledger.run(task_id="DL-zzzz", judge=False)
    assert rep["usage_error"] is True
    assert "unknown task" in rep["error"]
    rep = audit_ledger.run(task_id="DL-bbbb", judge=False)
    assert rep["usage_error"] is True
    assert "not done" in rep["error"]
    rep = audit_ledger.run(task_id="DL-aa", judge=False)  # unique prefix
    assert rep["summary"]["total"] == 1
    assert rep["tasks"][0]["id"] == "DL-aaaa"


def test_limit_must_be_positive(monkeypatch):
    _stub_ledger(monkeypatch, {"DL-aaaa": _task()})
    rep = audit_ledger.run(limit=0, judge=False)
    assert rep["usage_error"] is True
    assert audit_ledger.exit_code(rep) == 2


# ---------------------------------------------------------------------------
# deterministic layer
# ---------------------------------------------------------------------------


def test_pass_with_resolved_anchors(monkeypatch, git_repo):
    repo, sha = git_repo
    _stub_ledger(
        monkeypatch,
        {
            "DL-ab12": _task(
                evidence=f"commit:{sha} file:tracked.txt",
                met=[1],
            )
        },
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=str(repo), judge=False)
    assert rep["summary"]["total"] == 1
    assert rep["tasks"][0]["verdict"] == audit_ledger.PASS
    assert rep["tasks"][0]["anchor_state"] == "supported"
    assert audit_ledger.exit_code(rep) == 0


def test_violation_on_missing_commit_anchor(monkeypatch, git_repo):
    repo, _sha = git_repo
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="commit:" + "0" * 40, met=[1])},
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=str(repo), judge=False)
    row = rep["tasks"][0]
    assert row["verdict"] == audit_ledger.VIOLATION
    assert row["anchor_state"] == "contradicted"
    assert row["anchors"][0]["status"] == "missing"
    assert audit_ledger.exit_code(rep) == 1


def test_violation_on_missing_file_anchor(monkeypatch, git_repo):
    repo, _sha = git_repo
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="file:no-such-file.txt", met=[1])},
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=str(repo), judge=False)
    assert rep["tasks"][0]["verdict"] == audit_ledger.VIOLATION


def test_commit_anchor_unverified_without_git_repo(monkeypatch, tmp_path):
    """No git repo → a missing-object claim can never be proven →
    UNVERIFIED, never VIOLATION on doubt."""
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="commit:" + "f" * 40, met=[1])},
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=str(tmp_path), judge=False)
    row = rep["tasks"][0]
    assert row["anchors"][0]["status"] == "unverified"
    assert row["verdict"] == audit_ledger.UNVERIFIED


def test_unstructured_evidence_is_unverified(monkeypatch):
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="tests passed, all green", met=[1])},
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=".", judge=False)
    row = rep["tasks"][0]
    assert row["anchor_state"] == "unverifiable"
    assert row["unstructured_tokens"] > 0
    assert row["verdict"] == audit_ledger.UNVERIFIED


def test_url_anchor_needs_online(monkeypatch):
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="url:https://example.com/x", met=[1])},
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=".", judge=False)
    anchor = rep["tasks"][0]["anchors"][0]
    assert anchor["status"] == "unverified"
    assert anchor["needs_online"] is True
    assert rep["tasks"][0]["verdict"] == audit_ledger.UNVERIFIED


def test_pr_anchor_always_unverified(monkeypatch):
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="pr:https://github.com/x/y/pull/1",
                          met=[1])},
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=".", judge=False, online=True)
    assert rep["tasks"][0]["anchors"][0]["status"] == "unverified"


def test_file_under_tmp_is_ephemeral(monkeypatch):
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="file:/tmp/shot.png", met=[1])},
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=".", judge=False)
    anchor = rep["tasks"][0]["anchors"][0]
    assert anchor["status"] == "ephemeral"
    assert rep["tasks"][0]["verdict"] == audit_ledger.UNVERIFIED


def test_partial_coverage_with_supported_anchor(monkeypatch, git_repo):
    repo, sha = git_repo
    _stub_ledger(
        monkeypatch,
        {
            "DL-ab12": _task(
                accept=["tests pass", "docs updated"],
                evidence=f"commit:{sha}",
                met=[1],
            )
        },
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=str(repo), judge=False)
    row = rep["tasks"][0]
    assert row["coverage"] == "partial"
    assert row["verdict"] == audit_ledger.PARTIAL


def test_waived_criteria_count_as_covered(monkeypatch, git_repo):
    repo, sha = git_repo
    _stub_ledger(
        monkeypatch,
        {
            "DL-ab12": _task(
                accept=["tests pass", "docs updated"],
                evidence=f"commit:{sha}",
                met=[1],
                waived="user waived docs",
                waived_by="user:Icaro",
            )
        },
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=str(repo), judge=False)
    row = rep["tasks"][0]
    assert row["coverage"] == "full"
    assert row["verdict"] == audit_ledger.PASS
    assert row["criteria"][1]["waived"] is True


def test_glued_commit_tokens_split(monkeypatch, git_repo):
    """``commit:a,commit:b`` is two tokens — one bad hash must not be
    masked by a good one (and vice versa)."""
    repo, sha = git_repo
    _stub_ledger(
        monkeypatch,
        {
            "DL-ab12": _task(
                evidence=f"commit:{sha},commit:{'0' * 40}", met=[1]
            )
        },
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=str(repo), judge=False)
    row = rep["tasks"][0]
    kinds = {a["status"] for a in row["anchors"]}
    assert "resolved" in kinds and "missing" in kinds
    assert row["verdict"] == audit_ledger.VIOLATION


# ---------------------------------------------------------------------------
# semantic layer (judge)
# ---------------------------------------------------------------------------


def test_judge_confirms_without_anchors_gives_pass(monkeypatch):
    """Free-text evidence + full coverage + judge confirms → PASS: the
    semantic layer IS the verification when no anchor resolves."""
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="generate_wiki ran, 45 repos listed",
                          met=[1])},
    )
    client = _FakeClient(
        {"c1": _Ans(True, 0.9), "contradiction": _Ans(False, 0.1)}
    )
    _fake_judge(monkeypatch, client)
    rep = audit_ledger.run(repo=".", judge=True)
    row = rep["tasks"][0]
    assert row["judge"]["state"] == "confirms"
    assert row["verdict"] == audit_ledger.PASS
    assert len(client.calls) == 1


def test_judge_denies_criterion_is_violation(monkeypatch):
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="some free text", met=[1])},
    )
    client = _FakeClient(
        {"c1": _Ans(False, 0.2), "contradiction": _Ans(False, 0.1)}
    )
    _fake_judge(monkeypatch, client)
    rep = audit_ledger.run(repo=".", judge=True)
    assert rep["tasks"][0]["verdict"] == audit_ledger.VIOLATION


def test_judge_contradiction_is_violation(monkeypatch):
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="some free text", met=[1])},
    )
    client = _FakeClient(
        {"c1": _Ans(True, 0.9), "contradiction": _Ans(True, 0.8)}
    )
    _fake_judge(monkeypatch, client)
    rep = audit_ledger.run(repo=".", judge=True)
    assert rep["tasks"][0]["verdict"] == audit_ledger.VIOLATION


def test_dl309f_public_private_scope_mismatch_is_violation(monkeypatch):
    """DL-309f regression: a ``done`` claim about the PUBLIC deepwiki
    backed only by PRIVATE/org indexing evidence is a scope mismatch —
    the judge's contradiction probe must surface it as VIOLATION."""
    _stub_ledger(
        monkeypatch,
        {
            "DL-309f": _task(
                tid="DL-309f",
                title="Index public repos on deepwiki.com (public wiki)",
                accept=["5 priority public repos indexed on deepwiki.com"],
                evidence="generate_wiki indexed 45 repos in the private "
                "org wiki (app.devin.ai/wiki); public deepwiki.com "
                "still shows 0 indexed",
                met=[1],
            )
        },
    )
    client = _FakeClient(
        {"c1": _Ans(False, 0.15), "contradiction": _Ans(True, 0.85)}
    )
    _fake_judge(monkeypatch, client)
    rep = audit_ledger.run(repo=".", judge=True)
    row = rep["tasks"][0]
    assert row["id"] == "DL-309f"
    assert row["verdict"] == audit_ledger.VIOLATION
    # the judge actually saw the public/private scope text
    state_text, _questions = client.calls[0]
    assert "deepwiki.com" in state_text
    assert "private" in state_text


def test_judge_abstains_is_unverified_never_violation(monkeypatch):
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="some free text", met=[1])},
    )
    client = _FakeClient(
        {"c1": _Ans(True, 0.55, abstained=True),
         "contradiction": _Ans(False, 0.1)}
    )
    _fake_judge(monkeypatch, client)
    rep = audit_ledger.run(repo=".", judge=True)
    row = rep["tasks"][0]
    assert row["judge"]["state"] == "abstains"
    assert row["verdict"] == audit_ledger.UNVERIFIED


def test_judge_unavailable_fails_open_to_unverified(monkeypatch, git_repo):
    repo, sha = git_repo
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence=f"commit:{sha}", met=[1])},
    )
    monkeypatch.setattr(
        audit_ledger,
        "_build_judge",
        lambda: (None, None, "poordjaevin not installed"),
    )
    rep = audit_ledger.run(repo=str(repo), judge=True)
    row = rep["tasks"][0]
    assert row["judge"]["state"] == "unavailable"
    # even with a supported anchor the semantic layer did not decide →
    # UNVERIFIED, never PASS without the judge.
    assert row["anchor_state"] == "supported"
    assert row["verdict"] == audit_ledger.UNVERIFIED
    assert rep["judge"]["available"] is False


def test_judge_backend_error_circuits_remaining_tasks(monkeypatch):
    tasks = {
        "DL-aaaa": _task(tid="DL-aaaa", evidence="text a", met=[1]),
        "DL-bbbb": _task(tid="DL-bbbb", evidence="text b", met=[1],
                         updated="2026-11-16T01:00:00Z"),
    }
    _stub_ledger(monkeypatch, tasks)
    client = _FakeClient(exc=RuntimeError("bridge down"))
    _fake_judge(monkeypatch, client)
    rep = audit_ledger.run(repo=".", judge=True)
    assert len(client.calls) == 1  # circuit breaker: one failure only
    assert all(t["judge"]["state"] == "unavailable" for t in rep["tasks"])
    assert all(t["verdict"] == audit_ledger.UNVERIFIED for t in rep["tasks"])


def test_judge_only_asks_declared_met_criteria(monkeypatch):
    _stub_ledger(
        monkeypatch,
        {
            "DL-ab12": _task(
                accept=["tests pass", "docs updated"],
                evidence="free text",
                met=[1],
                waived="docs waived",
                waived_by="user:x",
            )
        },
    )
    client = _FakeClient(
        {"c1": _Ans(True, 0.9), "contradiction": _Ans(False, 0.1)}
    )
    _fake_judge(monkeypatch, client)
    rep = audit_ledger.run(repo=".", judge=True)
    _state, questions = client.calls[0]
    assert set(questions) == {"c1", "contradiction"}
    row = rep["tasks"][0]
    assert row["criteria"][0]["judge"]["value"] is True
    assert row["criteria"][1]["judge"] is None  # waived → not asked
    assert row["verdict"] == audit_ledger.PASS


def test_anchor_violation_wins_over_judge(monkeypatch, git_repo):
    repo, _sha = git_repo
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="commit:" + "0" * 40, met=[1])},
    )
    client = _FakeClient(
        {"c1": _Ans(True, 0.9), "contradiction": _Ans(False, 0.1)}
    )
    _fake_judge(monkeypatch, client)
    rep = audit_ledger.run(repo=str(repo), judge=True)
    assert rep["tasks"][0]["verdict"] == audit_ledger.VIOLATION


# ---------------------------------------------------------------------------
# output shape + exit codes + CLI wiring
# ---------------------------------------------------------------------------


def test_json_shape(monkeypatch, git_repo, capsys):
    repo, sha = git_repo
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence=f"commit:{sha}", met=[1])},
    )
    _no_judge(monkeypatch)
    assert audit_ledger.main(
        ["--repo", str(repo), "--no-judge", "--json"]
    ) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["version"] == audit_ledger.__version__
    assert rep["summary"] == {
        "total": 1, "PASS": 1, "PARTIAL": 0, "UNVERIFIED": 0,
        "VIOLATION": 0,
    }
    row = rep["tasks"][0]
    for key in ("id", "title", "status", "verdict", "criteria",
                "anchors", "judge"):
        assert key in row
    assert row["verdict"] == "PASS"


def test_render_text_lists_verdicts(monkeypatch):
    _stub_ledger(
        monkeypatch,
        {
            "DL-aaaa": _task(tid="DL-aaaa", evidence="commit:" + "0" * 40,
                             met=[1], updated="2026-11-16T02:00:00Z"),
            "DL-bbbb": _task(tid="DL-bbbb", evidence="free text",
                             met=[1], updated="2026-11-16T01:00:00Z"),
        },
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=".", judge=False)
    out = audit_ledger.render_text(rep)
    assert "VIOLATION" in out and "UNVERIFIED" in out
    assert "MISSING commit:" in out


def test_exit_codes(monkeypatch, git_repo):
    repo, sha = git_repo
    _stub_ledger(
        monkeypatch,
        {
            "DL-aaaa": _task(tid="DL-aaaa", evidence=f"commit:{sha}",
                             met=[1], updated="2026-11-16T02:00:00Z"),
            "DL-bbbb": _task(tid="DL-bbbb", evidence="free text",
                             met=[1], updated="2026-11-16T01:00:00Z"),
        },
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=str(repo), judge=False)
    assert rep["summary"]["PASS"] == 1
    assert rep["summary"]["UNVERIFIED"] == 1
    assert audit_ledger.exit_code(rep) == 1


def test_limit_selects_most_recently_updated(monkeypatch):
    _stub_ledger(
        monkeypatch,
        {
            "DL-aaaa": _task(tid="DL-aaaa", evidence="x",
                             updated="2026-11-15T00:00:00Z"),
            "DL-bbbb": _task(tid="DL-bbbb", evidence="x",
                             updated="2026-11-16T00:00:00Z"),
        },
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(repo=".", judge=False, limit=1)
    assert rep["summary"]["total"] == 1
    assert rep["tasks"][0]["id"] == "DL-bbbb"


def test_cli_dispatch_calls_run(monkeypatch):
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return {
            "version": "0.0.0", "ledger_dir": ".", "repo": "/tmp/r",
            "judge": {"enabled": False, "available": False,
                      "backend": None, "detail": None},
            "tasks": [{
                "id": "DL-ab12", "title": "t", "status": "done",
                "verdict": "PASS", "coverage": "full",
                "anchor_state": "supported", "unstructured_tokens": 0,
                "anchors": [], "criteria": [],
                "judge": {"state": "off", "backend": None,
                          "detail": None, "results": None},
                "evidence": "", "met": [1], "waived": "",
                "waived_by": "",
            }],
            "summary": {"total": 1, "PASS": 1, "PARTIAL": 0,
                        "UNVERIFIED": 0, "VIOLATION": 0},
        }

    monkeypatch.setattr(audit_ledger, "run", fake_run)
    rc = cli_main(
        ["audit-ledger", "--repo", "/tmp/r", "--task", "DL-ab12",
         "--limit", "3", "--no-judge", "--json"]
    )
    assert rc == 0
    assert seen["repo"] == ["/tmp/r"]  # --repo is repeatable → list
    assert seen["task_id"] == "DL-ab12"
    assert seen["limit"] == 3
    assert seen["judge"] is False


def test_multi_repo_commit_resolves_in_second_repo(
    monkeypatch, git_repo, tmp_path
):
    """A commit that lives in repo B but not repo A is NOT a
    contradiction — the ledger spans repos, so anchors resolve against
    every candidate before ``missing`` is declared."""
    repo_a, _sha_a = git_repo
    repo_b = tmp_path / "repo-b"
    repo_b.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo_b, check=True)
    (repo_b / "b.txt").write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_b), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo_b), "-c", "user.email=f@f",
         "-c", "user.name=f", "commit", "-qm", "b"],
        check=True,
    )
    sha_b = subprocess.run(
        ["git", "-C", str(repo_b), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence=f"commit:{sha_b}", met=[1])},
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(
        repo=[str(repo_a), str(repo_b)], judge=False
    )
    anchor = rep["tasks"][0]["anchors"][0]
    assert anchor["status"] == "resolved"
    assert anchor["repo"] == str(repo_b)
    assert rep["tasks"][0]["verdict"] == audit_ledger.PASS


def test_commit_missing_in_all_repos_is_violation(
    monkeypatch, git_repo, tmp_path
):
    repo_a, _sha = git_repo
    repo_b = tmp_path / "repo-b2"
    repo_b.mkdir()
    _stub_ledger(
        monkeypatch,
        {"DL-ab12": _task(evidence="commit:" + "0" * 40, met=[1])},
    )
    _no_judge(monkeypatch)
    rep = audit_ledger.run(
        repo=[str(repo_a), str(repo_b)], judge=False
    )
    # repo_b is not a git repo → skipped; repo_a lacks the object →
    # missing in every git candidate → VIOLATION.
    assert rep["tasks"][0]["verdict"] == audit_ledger.VIOLATION
