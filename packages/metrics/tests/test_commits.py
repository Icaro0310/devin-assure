"""commits: session→commit correlation from tool_call_state payloads."""

from __future__ import annotations

import json
import sqlite3

import pytest
from devin_internals.fixtures import create_sessions_db
from devin_metrics.aggregate import commits_report, summarize
from devin_metrics.cli import main
from devin_metrics.collect import collect
from devin_metrics.render import render_commits_md, render_summary_md

from tests.conftest import (
    REPO_ALPHA,
    REPO_BETA,
    SESSION_PLAN,
    SHA_A,
    SHA_B,
    SHA_C,
    _add_children,
    _add_session,
    _wipe_generated_rows,
)


@pytest.fixture
def snap(sessions_db, acp_dir):
    return collect(sessions_db, acp_dir)


# -- collect -----------------------------------------------------------------


def test_collect_extracts_commit_refs(snap, session_ids):
    by_session: dict[str, list] = {}
    for ref in snap.commits:
        by_session.setdefault(ref.session_id, []).append(ref)
    assert set(by_session) == {session_ids[0], session_ids[2]}
    s0 = by_session[session_ids[0]]
    assert [(r.sha, r.repo_url) for r in s0] == [(SHA_A, REPO_ALPHA)]
    s2 = by_session[session_ids[2]]
    # SHA_C appears in two tool calls -> two refs, one unique sha
    assert sorted(r.sha for r in s2) == [SHA_B, SHA_C, SHA_C]
    repos = {r.sha: r.repo_url for r in s2}
    assert repos[SHA_B] == REPO_BETA
    assert repos[SHA_C] is None  # bare sha, no /commit/ URL context
    # sessions 1 and 3 carry no commit references
    assert session_ids[1] not in by_session
    assert session_ids[3] not in by_session


def test_short_shas_are_ignored(snap):
    # "[main 0123456]" outputs carry 7-hex ids only — never extracted
    assert all(len(r.sha) == 40 for r in snap.commits)


# -- aggregate ---------------------------------------------------------------


def test_commits_rollup(snap, session_ids):
    report = commits_report(snap)
    assert report["sessions_with_commits"] == 2
    assert report["unique_shas"] == 3
    assert report["commit_refs"] == 4  # SHA_C counted once per tool call
    sessions = {s["id"]: s for s in report["sessions"]}
    s0 = sessions[session_ids[0]]
    assert s0["title"] == "alpha-1"
    assert s0["project"] == "/work/alpha"
    assert s0["n_commits"] == 1
    assert s0["shas"] == [SHA_A]
    assert s0["repos"] == [REPO_ALPHA]
    s2 = sessions[session_ids[2]]
    assert s2["n_commits"] == 2
    assert s2["shas"] == [SHA_B, SHA_C]  # sorted
    assert s2["repos"] == [REPO_BETA]


def test_commits_rollup_repos(snap):
    report = commits_report(snap)
    repos = {r["repo"]: r for r in report["repos"]}
    # SHA_C has no repo context -> counted in unique_shas, not in any repo
    assert set(repos) == {REPO_ALPHA, REPO_BETA}
    assert repos[REPO_ALPHA]["unique_shas"] == 1
    assert repos[REPO_ALPHA]["sessions"] == 1
    assert repos[REPO_BETA]["unique_shas"] == 1
    assert repos[REPO_BETA]["sessions"] == 1


def test_commits_top_sessions_sorted_by_commit_count(snap, session_ids):
    report = commits_report(snap)
    top = report["top_sessions"]
    assert top[0]["id"] == session_ids[2]  # 2 commits > 1
    assert top[0]["n_commits"] == 2
    assert top == report["sessions"][:5]


def test_summary_reports_commit_counts(snap):
    s = summarize(snap)
    assert s["sessions_with_commits"] == 2
    assert s["unique_commit_shas"] == 3


# -- render ------------------------------------------------------------------


def test_render_commits_md(snap, session_ids):
    md = render_commits_md(commits_report(snap))
    assert "## devin-metrics commits" in md
    assert SHA_A[:7] in md and SHA_B[:7] in md and SHA_C[:7] in md
    assert REPO_ALPHA in md and REPO_BETA in md
    assert "alpha-1" in md and "beta-1" in md


def test_summary_md_lists_commit_lines(snap):
    md = render_summary_md(summarize(snap))
    assert "Sessions with commits" in md
    assert "Unique commit SHAs" in md


# -- cli ---------------------------------------------------------------------


def test_commits_cli_table(sessions_db, acp_dir, capsys):
    rc = main([
        "commits", "--sessions-db", str(sessions_db),
        "--acp-dir", str(acp_dir),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert SHA_A[:7] in out
    assert SHA_C[:7] in out
    assert REPO_ALPHA in out and REPO_BETA in out


def test_commits_cli_json(sessions_db, acp_dir, session_ids, capsys):
    rc = main([
        "commits", "--sessions-db", str(sessions_db),
        "--acp-dir", str(acp_dir), "--json",
    ])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["sessions_with_commits"] == 2
    assert data["unique_shas"] == 3
    assert {s["id"] for s in data["sessions"]} == {
        session_ids[0], session_ids[2],
    }
    assert set(data["sessions"][0]) == {
        "id", "title", "project", "n_commits", "shas", "repos",
    }
    assert set(data["repos"][0]) == {"repo", "unique_shas", "sessions"}


# -- empty case ----------------------------------------------------------------


def _no_commit_db(tmp_path):
    db = create_sessions_db(tmp_path / "cli" / "sessions.db")
    _wipe_generated_rows(db)
    con = sqlite3.connect(db)
    with con:
        _add_session(con, "s-empty", SESSION_PLAN[0])
        _add_children(con, "s-empty", 1, 1)
    con.close()
    return db


def test_commits_empty_markdown(tmp_path, capsys):
    db = _no_commit_db(tmp_path)
    rc = main([
        "commits", "--sessions-db", str(db),
        "--acp-dir", str(tmp_path / "acp"),
    ])
    captured = capsys.readouterr()
    assert rc == 0
    assert "no commit references" in captured.out


def test_commits_empty_json(tmp_path, capsys):
    db = _no_commit_db(tmp_path)
    rc = main([
        "commits", "--sessions-db", str(db),
        "--acp-dir", str(tmp_path / "acp"), "--json",
    ])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["sessions_with_commits"] == 0
    assert data["unique_shas"] == 0
    assert data["commit_refs"] == 0
    assert data["sessions"] == []
    assert data["repos"] == []
