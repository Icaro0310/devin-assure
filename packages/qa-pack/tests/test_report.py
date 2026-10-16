"""report.py — per-session verdicts and the serializable audit shape."""

from devin_internals.parsers import SessionsStore

from devin_qa_pack.report import (
    PARTIAL,
    PASS,
    UNVERIFIED,
    audit_all,
    audit_session,
    audits_payload,
)


def _session(store, sid):
    return next(s for s in store.sessions() if s.id == sid)


def test_verified_session_is_pass(sessions_db):
    with SessionsStore(sessions_db) as store:
        audit = audit_session(store, _session(store, "sess-verified"))
    assert audit.verdict == PASS
    assert audit.results
    assert all(r.status == "verified" for r in audit.results)


def test_disputed_session_is_partial(sessions_db):
    with SessionsStore(sessions_db) as store:
        audit = audit_session(store, _session(store, "sess-disputed"))
    assert audit.verdict == PARTIAL
    assert all(r.status == "disputed" for r in audit.results)


def test_session_with_only_uncheckable_claims_is_unverified(sessions_db):
    with SessionsStore(sessions_db) as store:
        audit = audit_session(store, _session(store, "sess-unverifiable"))
    assert audit.verdict == UNVERIFIED
    assert all(r.status == "unverifiable" for r in audit.results)


def test_session_with_opaque_tool_calls_is_unverified(sessions_db):
    with SessionsStore(sessions_db) as store:
        audit = audit_session(store, _session(store, "sess-opaque"))
    assert audit.verdict == UNVERIFIED
    assert {r.claim.kind for r in audit.results} == {"tests"}
    assert audit.results[0].status == "unverifiable"


def test_audit_all_respects_limit(sessions_db):
    with SessionsStore(sessions_db) as store:
        audits = audit_all(store)
        limited = audit_all(store, limit=2)
    assert len(audits) == 5
    assert len(limited) == 2


def test_http_session_is_pass(sessions_db):
    with SessionsStore(sessions_db) as store:
        audit = audit_session(store, _session(store, "sess-http"))
    assert audit.verdict == PASS
    assert [(r.claim.kind, r.claim.detail) for r in audit.results] == [
        ("http", "200")
    ]
    assert audit.results[0].status == "verified"


def test_payload_shape(sessions_db):
    with SessionsStore(sessions_db) as store:
        payload = audits_payload([audit_session(store, _session(store, "sess-verified"))],
                               sessions_db)
    assert payload["sessions_db"].endswith("sessions.db")
    (entry,) = payload["sessions"]
    assert entry["session_id"] == "sess-verified"
    assert entry["verdict"] == "PASS"
    assert set(entry["summary"]) == {"verified", "disputed", "unverifiable"}
    for c in entry["claims"]:
        assert set(c) == {"kind", "detail", "status", "evidence",
                          "node_id", "excerpt"}


def test_session_with_no_claims_is_unverified(sessions_db, tmp_path):
    import sqlite3

    from conftest import add_message, add_session

    con = sqlite3.connect(sessions_db)
    with con:
        add_session(con, "sess-quiet", str(tmp_path / "nope"), "Quiet")
        add_message(con, "sess-quiet", 1, "agent", "done, nothing to report")
    con.close()
    with SessionsStore(sessions_db) as store:
        audit = audit_session(store, _session(store, "sess-quiet"))
    assert audit.verdict == UNVERIFIED
