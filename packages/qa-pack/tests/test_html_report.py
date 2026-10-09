"""html_report.py — self-contained static HTML rendering of audits."""

from devin_internals.parsers import SessionsStore

from devin_qa_pack.html_report import render_html
from devin_qa_pack.report import audit_all, audit_session


def _audits(sessions_db):
    with SessionsStore(sessions_db) as store:
        return audit_all(store)


def test_renders_self_contained_page(sessions_db):
    html = render_html(_audits(sessions_db), sessions_db)
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html and "</style>" in html
    assert "<script" not in html  # zero JavaScript
    # no external assets: nothing references a URL or a linked file
    assert 'src="' not in html and 'href="' not in html
    assert "http://" not in html and "https://" not in html


def test_contains_verdict_counts_and_sessions(sessions_db):
    html = render_html(_audits(sessions_db), sessions_db)
    for verdict in ("PASS", "PARTIAL", "UNVERIFIED"):
        assert verdict in html
    for sid in ("sess-verified", "sess-disputed", "sess-unverifiable",
                "sess-opaque", "sess-http"):
        assert sid in html
    # per-claim breakdown: kind/detail/status for the http claim
    assert "http" in html and "200" in html
    assert "verified" in html and "disputed" in html


def test_deterministic_output(sessions_db):
    audits = _audits(sessions_db)
    assert render_html(audits, "db.sqlite") == render_html(audits, "db.sqlite")


def test_escapes_hostile_content(sessions_db, tmp_path):
    import sqlite3

    from conftest import add_message, add_session

    con = sqlite3.connect(sessions_db)
    with con:
        add_session(con, "sess-evil", str(tmp_path / "nope"),
                    '<img onerror="x">')
        add_message(con, "sess-evil", 1, "agent",
                    "updated <b>evil</b>/report.html")
    con.close()
    with SessionsStore(sessions_db) as store:
        session = next(s for s in store.sessions() if s.id == "sess-evil")
        html = render_html([audit_session(store, session)], sessions_db)
    assert '<img onerror="x">' not in html
    assert "&lt;img onerror=" in html
