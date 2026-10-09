"""session-end subcommand — live audit of the session that just ended.

Fail-soft contract: exit 0 whenever the command ran (PASS/PARTIAL/
UNVERIFIED/SKIPPED alike); non-zero only on usage errors. The verdict
travels in a side file, never into the session store.
"""

import io
import json

import pytest
from devin_internals.fixtures import create_sessions_db
from devin_qa_pack.cli import main
from devin_qa_pack.session_end import (
    SKIPPED,
    default_out_path,
    resolve_session_id,
    session_id_from_payload,
)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# -- session resolution --------------------------------------------------------


def test_session_end_defaults_to_most_recent(sessions_db, tmp_path, capsys):
    out = tmp_path / "v.json"
    rc = main(["session-end", "--sessions-db", str(sessions_db),
               "--out", str(out)])
    assert rc == 0
    payload = _read(out)
    assert payload["session_id"] == "sess-http"  # highest last_activity_at
    assert payload["verdict"] == "PASS"
    assert payload["claims"] and payload["audited_at"]
    line = capsys.readouterr().out.strip()
    assert line.startswith("qa session-end: PASS sess-http")
    assert line.endswith(str(out))


def test_session_end_explicit_id(sessions_db, tmp_path, capsys):
    out = tmp_path / "v.json"
    rc = main(["session-end", "--sessions-db", str(sessions_db),
               "--session-id", "sess-verified", "--out", str(out)])
    assert rc == 0
    payload = _read(out)
    assert payload["session_id"] == "sess-verified"
    assert payload["verdict"] == "PASS"


def test_session_end_id_from_stdin_payload(sessions_db, tmp_path, capsys,
                                           monkeypatch):
    monkeypatch.setattr(
        "sys.stdin", io.StringIO('{"session_id": "sess-disputed"}')
    )
    out = tmp_path / "v.json"
    rc = main(["session-end", "--sessions-db", str(sessions_db),
               "--out", str(out)])
    assert rc == 0
    payload = _read(out)
    assert payload["session_id"] == "sess-disputed"
    assert payload["verdict"] == "PARTIAL"


def test_session_end_id_from_env(sessions_db, tmp_path, monkeypatch):
    """DEVIN_SESSION_ID is the dispatcher's env channel."""
    monkeypatch.setenv("DEVIN_SESSION_ID", "sess-verified")
    sid = resolve_session_id(None, stdin_stream=io.StringIO(""))
    assert sid == "sess-verified"


def test_payload_key_spellings():
    assert session_id_from_payload({"session_id": "a"}) == "a"
    assert session_id_from_payload({"sessionId": "b"}) == "b"
    assert session_id_from_payload({"nope": 1}) is None
    assert session_id_from_payload("not-a-dict") is None


# -- side file ------------------------------------------------------------------


def test_default_out_path_under_data_dir(tmp_path):
    out = tmp_path / "dd"
    path = default_out_path("sess-verified", data_dir=out)
    assert path == out / "qa" / "sess-verified.json"


def test_session_end_writes_to_data_dir_qa(sessions_db, tmp_path, capsys):
    data_dir = tmp_path / "data"
    rc = main(["session-end", "--sessions-db", str(sessions_db),
               "--session-id", "sess-verified", "--data-dir", str(data_dir)])
    assert rc == 0
    payload = _read(data_dir / "qa" / "sess-verified.json")
    assert payload["session_id"] == "sess-verified"
    assert payload["verdict"] == "PASS"
    summary = capsys.readouterr().out
    assert "qa/sess-verified.json" in summary.replace("\\", "/")


def test_side_file_contract_keys(sessions_db, tmp_path, capsys):
    out = tmp_path / "v.json"
    main(["session-end", "--sessions-db", str(sessions_db),
          "--session-id", "sess-verified", "--out", str(out)])
    capsys.readouterr()
    payload = _read(out)
    for key in ("session_id", "verdict", "claims", "audited_at"):
        assert key in payload
    assert isinstance(payload["claims"], list)
    assert {
        "kind", "detail", "status", "evidence", "node_id", "excerpt"
    } <= set(payload["claims"][0])


def test_never_writes_into_store(sessions_db, tmp_path, capsys):
    """The store is untouched; output lands only under --data-dir."""
    before = sessions_db.stat().st_mtime_ns
    listing_before = sorted(p.name for p in sessions_db.parent.iterdir())
    data_dir = tmp_path / "elsewhere"
    rc = main(["session-end", "--sessions-db", str(sessions_db),
               "--session-id", "sess-verified", "--data-dir", str(data_dir)])
    capsys.readouterr()
    assert rc == 0
    assert sessions_db.stat().st_mtime_ns == before
    # nothing new inside the store dir — qa/ lives under --data-dir only
    assert sorted(p.name for p in sessions_db.parent.iterdir()) == sorted(
        listing_before + ["elsewhere"])
    assert not (sessions_db.parent / "qa").exists()
    assert (data_dir / "qa" / "sess-verified.json").is_file()


# -- bounding --------------------------------------------------------------------


def test_limit_caps_claims(sessions_db, tmp_path, capsys):
    out = tmp_path / "v.json"
    rc = main(["session-end", "--sessions-db", str(sessions_db),
               "--session-id", "sess-verified", "--limit", "1",
               "--out", str(out)])
    capsys.readouterr()
    assert rc == 0
    assert len(_read(out)["claims"]) == 1


# -- fail-soft ---------------------------------------------------------------------


def test_unknown_session_skips_softly(sessions_db, tmp_path, capsys):
    out = tmp_path / "v.json"
    rc = main(["session-end", "--sessions-db", str(sessions_db),
               "--session-id", "nope", "--out", str(out)])
    assert rc == 0
    payload = _read(out)
    assert payload["verdict"] == SKIPPED
    assert payload["session_id"] == "nope"
    assert payload["claims"] == []
    assert "unknown session" in payload["reason"]
    assert "SKIPPED nope" in capsys.readouterr().out


def test_ambiguous_prefix_skips_softly(sessions_db, tmp_path, capsys):
    out = tmp_path / "v.json"
    rc = main(["session-end", "--sessions-db", str(sessions_db),
               "--session-id", "sess", "--out", str(out)])
    assert rc == 0
    assert _read(out)["verdict"] == SKIPPED
    assert "ambiguous" in _read(out)["reason"]


def test_missing_db_skips_softly(tmp_path, capsys):
    out = tmp_path / "v.json"
    rc = main(["session-end", "--sessions-db", str(tmp_path / "gone.db"),
               "--session-id", "sess-x", "--out", str(out)])
    assert rc == 0
    payload = _read(out)
    assert payload["verdict"] == SKIPPED
    assert "not found" in payload["reason"]


def test_empty_store_skips_softly(tmp_path, capsys):
    db = create_sessions_db(tmp_path / "empty.db", n_sessions=0)
    out = tmp_path / "v.json"
    rc = main(["session-end", "--sessions-db", str(db),
               "--out", str(out)])
    assert rc == 0
    assert _read(out)["verdict"] == SKIPPED
    assert "SKIPPED" in capsys.readouterr().out


def test_usage_error_exits_two(sessions_db):
    with pytest.raises(SystemExit) as exc:
        main(["session-end", "--bogus-flag"])
    assert exc.value.code == 2
