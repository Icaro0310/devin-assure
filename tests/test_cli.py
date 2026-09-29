"""cli.py — audit subcommand, --json shape and exit codes."""

import json

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
    assert len(payload["sessions"]) == 4
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
