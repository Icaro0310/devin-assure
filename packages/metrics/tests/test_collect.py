"""collect: read sessions.db + acp-messages into a MetricsSnapshot."""

from __future__ import annotations

import pytest
from devin_internals.schema import SchemaError
from devin_metrics.collect import collect


def test_collect_sessions(sessions_db, acp_dir, session_ids):
    snap = collect(sessions_db, acp_dir)
    assert len(snap.sessions) == 4
    by_id = {s.id: s for s in snap.sessions}
    assert set(by_id) == set(session_ids)
    s0 = by_id[session_ids[0]]
    assert s0.working_directory == "/work/alpha"
    assert s0.model == "swe-2-high"
    assert s0.n_messages == 5
    assert s0.n_tool_calls == 3
    assert s0.duration_ms == 600_000


def test_collect_joins_acp_usage(sessions_db, acp_dir, session_ids):
    snap = collect(sessions_db, acp_dir)
    by_id = {s.id: s for s in snap.sessions}
    s0 = by_id[session_ids[0]]
    assert s0.cost_usd == pytest.approx(0.0300)
    assert s0.input_tokens == 300
    assert s0.output_tokens == 30
    # session 3 has no acp db -> cost stays unknown, not zero
    s3 = by_id[session_ids[3]]
    assert s3.cost_usd is None
    assert s3.input_tokens is None


def test_usage_records_keep_session_link(sessions_db, acp_dir, session_ids):
    snap = collect(sessions_db, acp_dir)
    assert len(snap.usage) == 5  # 4 matched + 1 orphan row
    matched = [u for u in snap.usage if u.session_id in set(session_ids)]
    assert len(matched) == 4
    orphan = [u for u in snap.usage if u.session_id not in set(session_ids)]
    assert len(orphan) == 1
    assert orphan[0].cost_usd == pytest.approx(0.0070)
    assert snap.matched_sessions == 3
    assert snap.orphan_dbs == 1
    assert snap.acp_db_files == 4
    assert snap.acp_db_errors == 0


def test_missing_acp_dir_degrades_gracefully(sessions_db, tmp_path):
    snap = collect(sessions_db, tmp_path / "no-acp")
    assert snap.acp_available is False
    assert snap.acp_db_files == 0
    assert all(s.cost_usd is None for s in snap.sessions)
    assert snap.usage == ()


def test_none_acp_dir_degrades_gracefully(sessions_db):
    snap = collect(sessions_db, None)
    assert snap.acp_available is False
    assert len(snap.sessions) == 4


def test_missing_sessions_db_raises(tmp_path):
    with pytest.raises(SchemaError):
        collect(tmp_path / "nope.db", None)


def test_unparseable_acp_db_counted_not_fatal(sessions_db, acp_dir):
    bad = acp_dir / "broken.db"
    bad.write_bytes(b"not sqlite")
    snap = collect(sessions_db, acp_dir)
    assert snap.acp_db_errors == 1
    assert snap.acp_db_files == 5


def test_noise_rows_do_not_leak_into_usage(sessions_db, acp_dir):
    snap = collect(sessions_db, acp_dir)
    # each acp db also holds one "user.message" noise row; none may appear
    assert all(u.kind == "agent.message" for u in snap.usage)
