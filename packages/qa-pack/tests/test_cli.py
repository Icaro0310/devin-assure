"""cli.py — audit subcommand, --json shape and exit codes."""

import json
import sqlite3

import pytest
from devin_internals.fixtures import _BASE_TS_MS, create_sessions_db
from devin_qa_pack.cli import main


def test_audit_verified_session_exits_zero(sessions_db, capsys):
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--session", "sess-verified"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PASS" in out
    assert "sess-verified" in out


def test_audit_disputed_session_exits_one(sessions_db, capsys):
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--session", "sess-disputed"])
    assert rc == 1
    assert "PARTIAL" in capsys.readouterr().out


def test_audit_unverifiable_session_exits_one(sessions_db, capsys):
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--session", "sess-unverifiable"])
    assert rc == 1
    assert "UNVERIFIED" in capsys.readouterr().out


def test_audit_all_json_shape(sessions_db, capsys):
    rc = main(["audit", "--sessions-db", str(sessions_db), "--all", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1  # not every session is PASS
    assert payload["sessions_db"].endswith("sessions.db")
    assert len(payload["sessions"]) == 5
    assert {s["verdict"] for s in payload["sessions"]} == {
        "PASS", "PARTIAL", "UNVERIFIED"
    }


def test_audit_all_limit(sessions_db, capsys):
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--all", "--limit", "2", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc in (0, 1)
    assert len(payload["sessions"]) == 2


def test_audit_all_passes_when_everything_verified(sessions_db, capsys):
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--session", "sess-verified", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    (entry,) = payload["sessions"]
    assert entry["verdict"] == "PASS"
    assert len(entry["claims"]) >= 3


def test_unknown_session_exits_two(sessions_db, capsys):
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--session", "nope"])
    assert rc == 2
    assert "nope" in capsys.readouterr().err


def test_missing_db_exits_two(tmp_path, capsys):
    rc = main(["audit", "--sessions-db", str(tmp_path / "gone.db"),
               "--session", "x"])
    assert rc == 2
    assert capsys.readouterr().err


def test_unique_session_prefix_resolves(sessions_db, capsys):
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--session", "sess-veri"])
    assert rc == 0
    assert "sess-verified" in capsys.readouterr().out


def test_audit_all_empty_store_exits_two(tmp_path, capsys):
    """An existing but empty sessions.db yields no audits — fail closed
    (``all([])`` would otherwise exit 0 and pass a commit gate on no
    evidence)."""
    db = create_sessions_db(tmp_path / "empty.db", n_sessions=0)
    rc = main(["audit", "--sessions-db", str(db), "--all"])
    assert rc == 2
    assert "no sessions" in capsys.readouterr().err


@pytest.fixture
def two_dir_db(tmp_path):
    """sessions.db where the newest session lives OUTSIDE the repo dir:

    - ``sess-repo``: working_directory ``<tmp>/repo``, PARTIAL (claims,
      no corroborating tool calls).
    - ``sess-elsewhere``: working_directory ``<tmp>/elsewhere``, newer and
      PASS — the unrelated session a bare ``--limit 1`` would audit.
    """
    from conftest import add_message, add_session, add_tool_call, exec_call

    repo = tmp_path / "repo"
    repo.mkdir()
    elsewhere = tmp_path / "elsewhere"
    db = create_sessions_db(tmp_path / "sessions.db", n_sessions=0)
    con = sqlite3.connect(db)
    with con:
        add_session(con, "sess-repo", str(repo), "This repo's session")
        add_message(con, "sess-repo", 1, "agent",
                    "All tests passed. Committed deadbee.")
        add_session(con, "sess-elsewhere", str(elsewhere), "Other project",
                    created=_BASE_TS_MS + 3_600_000)
        add_message(con, "sess-elsewhere", 1, "agent",
                    "All tests passed — pytest is green.")
        add_tool_call(con, "sess-elsewhere", "tc-ok",
                      exec_call("python -m pytest -q", output="5 passed"))
    con.close()
    return db, repo


def test_audit_within_scopes_to_repo(two_dir_db, capsys):
    """``--within .`` keeps only sessions whose working_directory is this
    repo — an unrelated project's newer PASS cannot pass the gate."""
    db, repo = two_dir_db
    rc = main(["audit", "--sessions-db", str(db), "--all", "--limit", "1",
               "--within", str(repo)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "sess-repo" in out
    assert "sess-elsewhere" not in out


def test_audit_without_within_newest_unrelated_passes(two_dir_db, capsys):
    """The bypass ``--within`` closes: bare ``--all --limit 1`` audits the
    newest session overall — here another project's PASS."""
    db, _repo = two_dir_db
    rc = main(["audit", "--sessions-db", str(db), "--all", "--limit", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "sess-elsewhere" in out


def test_audit_within_no_match_exits_two(two_dir_db, tmp_path, capsys):
    """No session under PATH → nothing audited → fail closed."""
    db, _repo = two_dir_db
    rc = main(["audit", "--sessions-db", str(db), "--all",
               "--within", str(tmp_path / "untouched")])
    assert rc == 2
    assert "no sessions" in capsys.readouterr().err


def test_audit_within_requires_all(two_dir_db, capsys):
    db, _repo = two_dir_db
    rc = main(["audit", "--sessions-db", str(db), "--session", "sess-repo",
               "--within", "."])
    assert rc == 2
    assert "--within" in capsys.readouterr().err


# -- report subcommand ---------------------------------------------------------


def test_report_writes_html_file(sessions_db, tmp_path, capsys):
    out = tmp_path / "report.html"
    rc = main(["report", "--sessions-db", str(sessions_db),
               "--out", str(out)])
    assert rc == 1  # not every session is PASS
    assert "5 session(s)" in capsys.readouterr().out
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html and "<script" not in html
    assert "sess-verified" in html and "sess-http" in html
    assert "PASS" in html and "PARTIAL" in html and "UNVERIFIED" in html


def test_report_is_deterministic(sessions_db, tmp_path, capsys):
    a = tmp_path / "a.html"
    b = tmp_path / "b.html"
    main(["report", "--sessions-db", str(sessions_db), "--out", str(a)])
    main(["report", "--sessions-db", str(sessions_db), "--out", str(b)])
    capsys.readouterr()
    assert a.read_bytes() == b.read_bytes()


def test_report_single_session(sessions_db, tmp_path, capsys):
    out = tmp_path / "one.html"
    rc = main(["report", "--sessions-db", str(sessions_db),
               "--session", "sess-verified", "--out", str(out)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert "sess-verified" in html
    assert "sess-disputed" not in html


def test_report_missing_db_exits_two(tmp_path, capsys):
    rc = main(["report", "--sessions-db", str(tmp_path / "gone.db")])
    assert rc == 2
    assert capsys.readouterr().err


def test_report_unknown_session_exits_two(sessions_db, tmp_path, capsys):
    rc = main(["report", "--sessions-db", str(sessions_db),
               "--session", "nope", "--out", str(tmp_path / "x.html")])
    assert rc == 2
    assert "nope" in capsys.readouterr().err


def test_version_flag_prints_version(capsys):
    from devin_qa_pack import __version__
    from devin_qa_pack.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert f"devin-qa-pack {__version__}" in capsys.readouterr().out
