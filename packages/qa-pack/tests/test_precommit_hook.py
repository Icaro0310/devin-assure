"""Contract test for the repo-level pre-commit hook manifest.

``.pre-commit-hooks.yaml`` at the repo root is what pre-commit reads when
this repo is used as a hook source (``repo:`` in a consumer's
``.pre-commit-config.yaml``). The pre-commit package itself is not a dev
dependency, so these tests pin the manifest fields that make the gate
behave correctly and then execute the entry's argv through ``cli.main``:

- ``entry`` must run ``devin-qa-pack audit --all --limit 1 --within .`` —
  ``--within .`` scopes the audit to sessions whose working_directory is
  the repo pre-commit runs in, so another project's newer session can
  neither pass nor block this repo's gate.
- ``always_run`` + ``pass_filenames: false``: the gate inspects the Devin
  data dir, not the staged files, and must run on every commit.
- exit semantics: 0 only when the newest in-repo session is PASS; 1 on
  PARTIAL/UNVERIFIED; 2 (fail closed) when nothing in scope can be
  audited.
"""

from __future__ import annotations

import re
import shlex
import sqlite3
from pathlib import Path

import pytest
from devin_internals.fixtures import _BASE_TS_MS, create_sessions_db

from devin_qa_pack.cli import main

HOOK_FILE = Path(__file__).resolve().parents[3] / ".pre-commit-hooks.yaml"

pytestmark = pytest.mark.skipif(
    not HOOK_FILE.exists(),
    reason="repo-level .pre-commit-hooks.yaml not present (sdist layout)",
)


def _manifest_fields() -> dict[str, str]:
    """Extract the single hook's scalar fields without a YAML dependency.

    The manifest is deliberately flat — one ``- id:`` list item whose
    fields are ``key: value`` scalars at two-space indent, plus a folded
    ``description`` block (``>-``) whose continuation lines are deeper and
    therefore never match the scalar pattern.
    """
    fields: dict[str, str] = {}
    for line in HOOK_FILE.read_text(encoding="utf-8").splitlines():
        item = re.match(r"^-\s+([a-z_]+):\s*(.+?)\s*$", line)
        if item:
            fields[item.group(1)] = item.group(2)
            continue
        scalar = re.match(r"^  ([a-z_]+):\s*(.+?)\s*$", line)
        if scalar:
            fields[scalar.group(1)] = scalar.group(2)
    return fields


def _entry_argv() -> list[str]:
    return shlex.split(_manifest_fields()["entry"])


def test_hook_manifest_contract():
    fields = _manifest_fields()
    assert fields["id"] == "devin-qa-pack-gate"
    assert fields["language"] == "system"
    # filenames are never appended — the audit inspects sessions.db, not
    # the staged files
    assert fields["pass_filenames"] == "false"
    # a commit gate must run even when nothing was staged for a fileset
    assert fields["always_run"] == "true"


def test_entry_scopes_audit_to_the_repo():
    """``.`` is what ties the audit to the repo pre-commit runs in."""
    argv = _entry_argv()
    assert argv[:2] == ["devin-qa-pack", "audit"]
    assert "--all" in argv and "--limit" in argv
    assert "--within" in argv
    assert argv[argv.index("--within") + 1] == "."
    # never opt the commit gate into network checks
    assert "--online" not in argv


@pytest.fixture
def repo_db(tmp_path):
    """sessions.db where the newest session is another project's PASS and
    this repo's newest session is PARTIAL — the exact shape that makes an
    unscoped ``--limit 1`` pass the gate on unrelated evidence."""
    from conftest import add_message, add_session, add_tool_call, exec_call

    repo = tmp_path / "repo"
    repo.mkdir()
    db = create_sessions_db(tmp_path / "sessions.db", n_sessions=0)
    con = sqlite3.connect(db)
    with con:
        add_session(con, "sess-repo", str(repo), "This repo's session")
        add_message(con, "sess-repo", 1, "agent",
                    "All tests passed. Committed deadbee.")
        add_session(con, "sess-elsewhere", str(tmp_path / "elsewhere"),
                    "Other project", created=_BASE_TS_MS + 3_600_000)
        add_message(con, "sess-elsewhere", 1, "agent",
                    "All tests passed — pytest is green.")
        add_tool_call(con, "sess-elsewhere", "tc-ok",
                      exec_call("python -m pytest -q", output="5 passed"))
    con.close()
    return db, repo


def test_entry_blocks_on_repo_verdict(repo_db, monkeypatch, capsys):
    """The entry argv drives ``cli.main``: this repo's PARTIAL newest
    session blocks the commit even though the globally newest session is
    an unrelated PASS."""
    db, repo = repo_db
    monkeypatch.chdir(repo)  # pre-commit runs hooks from the repo root
    rc = main(_entry_argv()[1:] + ["--sessions-db", str(db)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "sess-repo" in out
    assert "sess-elsewhere" not in out


def test_entry_fails_closed_without_repo_session(
        repo_db, tmp_path, monkeypatch, capsys):
    """No session under the repo → the gate exits 2 rather than passing
    on zero audited evidence."""
    db, _repo = repo_db
    fresh = tmp_path / "fresh-repo"
    fresh.mkdir()
    monkeypatch.chdir(fresh)
    rc = main(_entry_argv()[1:] + ["--sessions-db", str(db)])
    assert rc == 2
    assert "no sessions" in capsys.readouterr().err


def test_entry_fails_closed_on_empty_db(tmp_path, monkeypatch, capsys):
    """An existing but empty sessions.db must not exit 0."""
    db = create_sessions_db(tmp_path / "empty.db", n_sessions=0)
    monkeypatch.chdir(tmp_path)
    rc = main(_entry_argv()[1:] + ["--sessions-db", str(db)])
    assert rc == 2
    assert "no sessions" in capsys.readouterr().err
