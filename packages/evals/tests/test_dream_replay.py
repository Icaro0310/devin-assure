"""devin-evals dream replay: real sessions -> anonymized templates.

Fixtures follow the conftest approach: ``devin_internals.fixtures`` for
the real v17 DDL, then raw inserts of fully-known content so leak and
structure assertions are exact.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from devin_evals.dream.cli import main
from devin_evals.dream.defects import d01, d06
from devin_evals.dream.generate import write_sessions_db
from devin_evals.dream.replay import envelope_to_specs, replay_db
from devin_internals.fixtures import create_sessions_db
from devin_internals.parsers import SessionsStore

_BASE_TS_MS = 1_780_000_000_000

# distinctive content strings — every one must be absent from the template
SECRET = "hunter2-do-not-leak"
TITLE = "Top Secret Session Title"
WORKDIR = "/home/realuser/private-proj"
SESSION_ID = "real-session-0001"
MODEL = "real-model-name"
USER_TEXT = f"Please fix the deploy, the token is {SECRET}."
ASSISTANT_TEXT = "Done — restarted the private-proj service."


def insert_session(
    db_path: Path,
    *,
    session_id: str,
    title: str = TITLE,
    working_directory: str = WORKDIR,
    model: str = MODEL,
    metadata: str | None = None,
    nodes: tuple[tuple[int, int | None, str], ...] = (),
    tool_calls: tuple[tuple[str, dict | None, dict | None], ...] = (),
) -> None:
    """Append one session with explicit node ids/parents and raw blobs."""
    con = sqlite3.connect(db_path)
    with con:
        con.execute(
            "INSERT INTO sessions(id, working_directory, backend_type, model,"
            " agent_mode, created_at, last_activity_at, title, main_chain_id,"
            " shell_last_seen_index, cogs_json, workspace_dirs, hidden, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                working_directory,
                "real-backend",
                model,
                "interactive",
                _BASE_TS_MS,
                _BASE_TS_MS + 240_000,
                title,
                1,
                0,
                None,
                json.dumps([working_directory]),
                0,
                metadata,
            ),
        )
        for node_id, parent, blob in nodes:
            con.execute(
                "INSERT INTO message_nodes(session_id, node_id, parent_node_id,"
                " chat_message, created_at, metadata)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, node_id, parent, blob,
                 _BASE_TS_MS + node_id * 10_000, None),
            )
        for call_id, call, update in tool_calls:
            con.execute(
                "INSERT INTO tool_call_state(session_id, tool_call_id,"
                " tool_call_json, tool_call_update_json) VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    call_id,
                    None if call is None else json.dumps(call),
                    None if update is None else json.dumps(update),
                ),
            )
    con.close()


def _msg_blob(role: str, text: str, mid: str) -> str:
    return json.dumps({
        "message_id": mid, "role": role, "content": text, "metadata": {}})


@pytest.fixture
def real_db(tmp_path: Path) -> Path:
    """A 'real' sessions.db carrying identifiable content in every field."""
    db = create_sessions_db(tmp_path / "sessions.db", n_sessions=0)
    insert_session(
        db,
        session_id=SESSION_ID,
        metadata=json.dumps({"labels": ["prod", "kind:bugfix"],
                             "private": SECRET}),
        nodes=(
            (1, None, _msg_blob("user", USER_TEXT, "m1")),
            (2, 1, _msg_blob("assistant", ASSISTANT_TEXT, "m2")),
            # a branch off node 1 — forest linkage must survive
            (3, 1, _msg_blob("user", USER_TEXT, "m3")),
        ),
        tool_calls=(
            (
                "tc-real-1",
                {
                    "toolCallId": "tc-real-1",
                    "title": "Ran command",
                    "kind": "execute",
                    "rawInput": {"command": f"cat {SECRET}.conf"},
                    "_meta": {"cognition.ai/inferenceToolName": "exec"},
                },
                {
                    "toolCallId": "tc-real-1",
                    "status": "completed",
                    "content": [{
                        "type": "content",
                        "content": {"type": "text", "text": SECRET},
                    }],
                },
            ),
            ("tc-real-2", None, None),
        ),
    )
    return db


def test_replay_anonymizes_everything(real_db, tmp_path):
    out = tmp_path / "t.json"
    assert main(["replay", str(real_db), "-o", str(out)]) == 0
    text = out.read_text()
    # nothing identifiable survives — the whole point of replay
    for raw in (SECRET, TITLE, WORKDIR, SESSION_ID, MODEL,
                "real-backend", "private-proj", "realuser",
                "tc-real-1", "cat ", USER_TEXT, ASSISTANT_TEXT):
        assert raw not in text, raw
    t = json.loads(text)
    assert t["format"] == "devin-dream/session-template"
    s = t["sessions"][0]
    assert s["session_id"].startswith("sess_")
    assert s["labels"] == ["prod", "kind:bugfix"]  # labels preserved
    # structure preserved
    assert len(s["messages"]) == 3
    assert [m["role"] for m in s["messages"]] == [
        "user", "assistant", "user"]
    assert [m["parent_node_id"] for m in s["messages"]] == [None, 1, 1]
    assert len(s["tool_calls"]) == 2
    assert s["duration_ms"] == 240_000
    assert s["stats"]["schema_version"] == 17
    # placeholders are hash-keyed, not sequential
    msg = s["messages"][0]["chat_message"]
    assert msg["role"] == "user"               # enum survives
    assert msg["content"].startswith("msg_")
    # equal content -> equal placeholder (m1 and m3 carry the same text)
    assert s["messages"][2]["chat_message"]["content"] == msg["content"]
    # enum-ish tool fields preserved, content hashed, ids aligned
    call = s["tool_calls"][0]["call"]
    assert call["kind"] == "execute"
    assert call["rawInput"]["command"].startswith("str_")
    assert s["tool_calls"][0]["tool_call_id"].startswith("tc_")
    assert call["toolCallId"] == s["tool_calls"][0]["tool_call_id"]
    # NULL columns stay NULL
    assert s["tool_calls"][1] == {
        "tool_call_id": s["tool_calls"][1]["tool_call_id"],
        "call": None, "update": None}


def test_replay_deterministic_bytes(real_db, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert main(["replay", str(real_db), "-o", str(a)]) == 0
    assert main(["replay", str(real_db), "-o", str(b)]) == 0
    assert a.read_bytes() == b.read_bytes()


def test_replay_stdout(real_db, capsys):
    assert main(["replay", str(real_db)]) == 0
    t = json.loads(capsys.readouterr().out)
    assert len(t["sessions"]) == 1


def test_replay_session_id_selects_and_misses(real_db, tmp_path, capsys):
    insert_session(real_db, session_id="other-session", nodes=())
    out = tmp_path / "t.json"
    assert main(["replay", str(real_db), "--session-id", SESSION_ID,
                 "-o", str(out)]) == 0
    t = json.loads(out.read_text())
    assert len(t["sessions"]) == 1
    # both sessions replay when no selector is given
    assert main(["replay", str(real_db), "-o", str(out)]) == 0
    assert len(json.loads(out.read_text())["sessions"]) == 2
    capsys.readouterr()
    assert main(["replay", str(real_db), "--session-id", "nope"]) == 2
    assert "not found" in capsys.readouterr().err


def test_replay_empty_db_is_clean(tmp_path, capsys):
    db = create_sessions_db(tmp_path / "sessions.db", n_sessions=0)
    assert main(["replay", str(db)]) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["sessions"] == []
    assert "no sessions" in cap.err


def test_replay_missing_db_fails_loudly(tmp_path, capsys):
    assert main(["replay", str(tmp_path / "nope.db")]) == 2
    assert "replay:" in capsys.readouterr().err


def test_replay_corrupt_db_fails_loudly(tmp_path, capsys):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite database at all")
    assert main(["replay", str(bad)]) == 2
    assert "replay:" in capsys.readouterr().err


def test_replay_unknown_schema_fails_loudly(tmp_path, capsys):
    db = write_sessions_db(tmp_path / "drift.db", [d06()])  # v18
    assert main(["replay", str(db)]) == 2
    assert "unknown" in capsys.readouterr().err.lower()


def test_replay_roundtrip_emit_db(real_db, tmp_path):
    regen = tmp_path / "regen" / "sessions.db"
    assert main(["replay", str(real_db), "--emit-db", str(regen)]) == 0
    # the regenerated db parses under the same gated reader
    with SessionsStore(regen) as st:
        sessions = st.sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert s.id.startswith("sess_")
        meta = json.loads(s.metadata)
        assert "replay" in meta["labels"]
        assert "prod" in meta["labels"] and "kind:bugfix" in meta["labels"]
        nodes = st.message_nodes(s.id)
        assert len(nodes) == 3
        # forest linkage survived: node 3 still hangs off node 1
        assert [n.parent_node_id for n in nodes] == [None, 1, 1]
        roles = [json.loads(n.chat_message)["role"] for n in nodes]
        assert roles == ["user", "assistant", "user"]
        tcs = st.tool_call_state(s.id)
        assert len(tcs) == 2
        call = json.loads(tcs[0].tool_call_json)
        assert call["kind"] == "execute"
        assert call["toolCallId"] == tcs[0].tool_call_id
    # and nothing real leaked into the regenerated bytes either
    blob = regen.read_bytes()
    for raw in (SECRET.encode(), TITLE.encode(), SESSION_ID.encode()):
        assert raw not in blob


def test_replay_opaque_message_blobs(tmp_path):
    db = create_sessions_db(tmp_path / "sessions.db", n_sessions=0)
    insert_session(
        db,
        session_id="opaque",
        nodes=(
            (1, None, "definitely not json {"),
            (2, 1, json.dumps("a bare string message")),
        ),
    )
    t = replay_db(db, session_id="opaque")
    msgs = t["sessions"][0]["messages"]
    assert msgs[0]["role"] == "unknown"
    assert msgs[0]["chat_message"]["replay_opaque"].startswith("msg_")
    assert msgs[1]["chat_message"].startswith("msg_")
    assert "definitely not json" not in json.dumps(t)
    assert "a bare string message" not in json.dumps(t)


def test_envelope_validation_rejects_foreign_json(tmp_path):
    with pytest.raises(Exception, match="unknown template format"):
        envelope_to_specs({"format": "something-else"})
    with pytest.raises(Exception, match="format_version"):
        envelope_to_specs({
            "format": "devin-dream/session-template",
            "format_version": 99})


def test_dream_generated_db_replays(tmp_path):
    """Replay also works over dream's own output (dogfood path)."""
    dream_db = write_sessions_db(tmp_path / "dream.db", [d01()])
    t = replay_db(dream_db)
    s = t["sessions"][0]
    assert s["labels"] == ["synthetic"]
    assert s["stats"]["message_count"] == 2
