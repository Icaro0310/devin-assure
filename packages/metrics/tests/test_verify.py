"""commits --verify: integrity checks over recorded commit refs."""

from __future__ import annotations

import json
import sqlite3

from devin_metrics.cli import main
from devin_metrics.verify import verify_commits

from tests.conftest import SHA_A


def _insert_orphan_ref(db, session_id="ghost-session", sha=SHA_A):
    """Tamper: a tool_call_state row whose session does not exist."""
    con = sqlite3.connect(db)
    with con:
        con.execute(
            "INSERT INTO tool_call_state(session_id, tool_call_id,"
            " tool_call_json, tool_call_update_json)"
            " VALUES (?, ?, ?, ?)",
            (
                session_id,
                f"{session_id}-tc-0",
                json.dumps({"command": "git log"}),
                json.dumps({"output": f"parent {sha}"}),
            ),
        )
    con.close()


def test_verify_clean_store(sessions_db, acp_dir, capsys):
    rc = main(["commits", "--sessions-db", str(sessions_db),
               "--acp-dir", str(acp_dir), "--verify"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "clean" in out
    assert "MISMATCH" not in out


def test_verify_clean_json(sessions_db, acp_dir, capsys):
    rc = main(["commits", "--sessions-db", str(sessions_db),
               "--acp-dir", str(acp_dir), "--verify", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["status"] == "clean"
    assert data["commit_refs"] == 4
    assert data["unique_shas"] == 3
    assert {c["name"] for c in data["checks"]} == {
        "sha_format", "duplicate_refs", "orphan_refs",
        "counts", "rollups", "digest",
    }
    assert all(c["status"] == "pass" for c in data["checks"])
    assert len(data["digest"]) == 64


def test_verify_orphan_ref_is_mismatch(sessions_db, acp_dir, capsys):
    _insert_orphan_ref(sessions_db)
    rc = main(["commits", "--sessions-db", str(sessions_db),
               "--acp-dir", str(acp_dir), "--verify"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "MISMATCH" in out
    assert "orphan_refs" in out
    assert "ghost-session" in out


def test_verify_orphan_json_reports_mismatch(sessions_db, acp_dir, capsys):
    _insert_orphan_ref(sessions_db)
    rc = main(["commits", "--sessions-db", str(sessions_db),
               "--acp-dir", str(acp_dir), "--verify", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["status"] == "mismatch"
    orphan = next(c for c in data["checks"] if c["name"] == "orphan_refs")
    assert orphan["status"] == "fail"
    assert any("ghost-session" in m for m in orphan["mismatches"])
    # everything else still passes — the failure is scoped
    others = [c for c in data["checks"] if c["name"] != "orphan_refs"]
    assert all(c["status"] == "pass" for c in others)


def test_verify_detects_digest_drift(sessions_db, acp_dir):
    """A store mutated between collection and verification flips the
    digest check: snapshot collected first, db tampered after."""
    from devin_metrics.collect import collect

    snap = collect(sessions_db, acp_dir)
    _insert_orphan_ref(sessions_db, session_id="ghost-2")
    report = verify_commits(snap, sessions_db)
    assert report["status"] == "mismatch"
    digest = next(c for c in report["checks"] if c["name"] == "digest")
    assert digest["status"] == "fail"


def test_verify_empty_store_is_clean(tmp_path, capsys):
    from devin_internals.fixtures import create_sessions_db

    from tests.conftest import _wipe_generated_rows

    db = create_sessions_db(tmp_path / "cli" / "sessions.db")
    _wipe_generated_rows(db)
    rc = main(["commits", "--sessions-db", str(db),
               "--acp-dir", str(tmp_path / "acp"), "--verify"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "clean" in out
