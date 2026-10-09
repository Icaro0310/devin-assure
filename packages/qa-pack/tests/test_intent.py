"""intent.py (QA-4) — prompt intent vs. touched-path coverage.

Fixture sessions (built on the real v17 DDL via
``devin_internals.fixtures``):

- ``sess-aligned``:  prompt names src/app.py + tests/test_app.py, both
  touched → ``aligned``, exit 0.
- ``sess-missed``:   prompt names src/app.py + docs/guide.md, only the
  first touched → ``possibly_missed`` + ``flagged``, exit 1.
- ``sess-drift``:    prompt names src/app.py; agent also edits
  web/landing.html → ``scope_drift`` + ``flagged``, exit 1.
- ``sess-nopaths``:  prompt names nothing path-ish → ``skipped``.
- ``sess-nouser``:   no user-role message → ``skipped``.
"""

import json
import sqlite3
import types

import pytest
from devin_internals.fixtures import _BASE_TS_MS, create_sessions_db
from devin_internals.parsers.sessions import MessageNode, ToolCallState
from devin_qa_pack.cli import main
from devin_qa_pack.intent import (
    ALIGNED,
    FLAGGED,
    SKIPPED,
    audit_intent,
    extract_prompt_refs,
    extract_touched_paths,
    first_user_prompt,
    intent_dict,
    render_intent,
)

# -- fixture ------------------------------------------------------------------


def _edit(path: str) -> dict:
    return {
        "kind": "edit",
        "title": path,
        "status": "completed",
        "rawInput": {"path": path},
    }


@pytest.fixture
def intent_db(tmp_path):
    from conftest import add_message, add_session, add_tool_call

    wd = "/repo/qa-pack"
    db = create_sessions_db(tmp_path / "sessions.db", n_sessions=0)
    con = sqlite3.connect(db)
    with con:
        add_session(con, "sess-aligned", wd, "Aligned")
        add_message(con, "sess-aligned", 1, "user",
                    "Fix src/app.py and tests/test_app.py please.")
        add_tool_call(con, "sess-aligned", "tc-1", _edit("src/app.py"))
        add_tool_call(con, "sess-aligned", "tc-2",
                      _edit("tests/test_app.py"))

        add_session(con, "sess-missed", wd, "Missed",
                    created=_BASE_TS_MS + 3_600_000)
        add_message(con, "sess-missed", 1, "user",
                    "Update src/app.py and docs/guide.md.")
        add_tool_call(con, "sess-missed", "tc-1", _edit("src/app.py"))

        add_session(con, "sess-drift", wd, "Drift",
                    created=_BASE_TS_MS + 7_200_000)
        add_message(con, "sess-drift", 1, "user", "Fix src/app.py.")
        add_tool_call(con, "sess-drift", "tc-1", _edit("src/app.py"))
        add_tool_call(con, "sess-drift", "tc-2", _edit("web/landing.html"))

        add_session(con, "sess-nopaths", wd, "No paths",
                    created=_BASE_TS_MS + 10_800_000)
        add_message(con, "sess-nopaths", 1, "user", "make it faster")
        add_tool_call(con, "sess-nopaths", "tc-1", _edit("src/app.py"))

        add_session(con, "sess-nouser", wd, "No user",
                    created=_BASE_TS_MS + 14_400_000)
        add_message(con, "sess-nouser", 1, "agent",
                    "I edited src/app.py.")
        add_tool_call(con, "sess-nouser", "tc-1", _edit("src/app.py"))
    con.close()
    return db


# -- unit: prompt + touched extraction ----------------------------------------


def _node(role: str, text: str, node_id: int = 1) -> MessageNode:
    return MessageNode(
        row_id=node_id,
        session_id="s",
        node_id=node_id,
        parent_node_id=None,
        chat_message=json.dumps({"role": role, "text": text}),
        created_at=node_id,
        metadata=None,
    )


def _state(tcid: str, call: dict | None = None,
           update: dict | None = None) -> ToolCallState:
    return ToolCallState(
        session_id="s",
        tool_call_id=tcid,
        tool_call_json=json.dumps(call) if call is not None else None,
        tool_call_update_json=(
            json.dumps(update) if update is not None else None
        ),
    )


def test_first_user_prompt_picks_first_user_message():
    nodes = [
        _node("agent", "hello", 1),
        _node("user", "fix src/app.py", 2),
        _node("user", "also tests/x.py", 3),
    ]
    node = first_user_prompt(nodes)
    assert node is not None and node.node_id == 2


def test_first_user_prompt_none_when_no_user():
    assert first_user_prompt([_node("agent", "hi", 1)]) is None


def test_extract_refs_finds_files_and_dirs():
    refs = extract_prompt_refs("Fix src/app.py and the docs dir x/y/z.")
    paths = {r.path for r in refs}
    assert "src/app.py" in paths
    assert "x/y/z" in paths
    strong = {r.path for r in refs if r.strong}
    assert "src/app.py" in strong
    assert "x/y/z" not in strong  # extensionless dir is weak-only


def test_extract_refs_dotted_module():
    refs = extract_prompt_refs("update devin_qa_pack.intent now")
    mods = [r for r in refs if r.kind == "module"]
    assert any(r.path == "devin_qa_pack/intent" for r in mods)


def test_extract_refs_repo_name():
    refs = extract_prompt_refs(
        "touch the qa-pack repo", repo_names=["qa-pack"]
    )
    assert any(r.kind == "repo" and r.path == "qa-pack" for r in refs)


def test_extract_refs_ignores_slash_words_and_urls():
    refs = extract_prompt_refs(
        "fix it and/or read/write stuff, see https://x.test/a/b.py"
    )
    assert refs == [] or all(
        "and" != r.path and "b.py" not in r.path for r in refs
    )


def test_extract_touched_from_payloads_and_commands():
    states = [
        _state("t1", _edit("src/app.py")),
        _state("t2", {"kind": "execute",
                      "rawInput": {"command": "cat docs/readme.md"}}),
        _state("t3"),  # NULL payloads — contributes nothing
    ]
    touched = extract_touched_paths(states, "/repo/qa-pack")
    assert "/repo/qa-pack/src/app.py" in touched
    assert "/repo/qa-pack/docs/readme.md" in touched


def test_extract_touched_drops_vcs_internals():
    states = [_state("t1", {"kind": "execute",
                          "rawInput": {"command": "cat .git/config"}})]
    assert extract_touched_paths(states, "/repo") == set()


# -- unit: audit_intent on dataclasses ----------------------------------------


def _session(wd="/repo/qa-pack"):
    return types.SimpleNamespace(
        id="s", working_directory=wd, workspace_dirs=json.dumps([wd])
    )


def test_audit_aligned():
    audit = audit_intent(
        _session(),
        [_node("user", "fix src/app.py")],
        [_state("t1", _edit("src/app.py"))],
    )
    assert audit.status == ALIGNED
    assert audit.possibly_missed == []
    assert audit.scope_drift == []


def test_audit_missed_path():
    audit = audit_intent(
        _session(),
        [_node("user", "fix src/app.py and docs/guide.md")],
        [_state("t1", _edit("src/app.py"))],
    )
    assert audit.status == FLAGGED
    assert audit.possibly_missed == ["docs/guide.md"]


def test_audit_drift_path():
    audit = audit_intent(
        _session(),
        [_node("user", "fix src/app.py")],
        [_state("t1", _edit("src/app.py")),
         _state("t2", _edit("web/landing.html"))],
    )
    assert audit.status == FLAGGED
    assert audit.scope_drift == ["/repo/qa-pack/web/landing.html"]


def test_audit_skips_without_prompt_or_refs():
    a = audit_intent(_session(), [_node("agent", "hi")], [])
    assert a.status == SKIPPED and "user" in a.reason
    b = audit_intent(
        _session(), [_node("user", "make it faster")], [_state("t1")]
    )
    assert b.status == SKIPPED and "paths" in b.reason


def test_weak_refs_never_reported_missed():
    """Extensionless dir mentions can't produce a possibly_missed flag."""
    audit = audit_intent(
        _session(),
        [_node("user", "work under src/sub/dir only")],
        [_state("t1", _edit("other/file.py"))],
    )
    assert audit.possibly_missed == []


def test_bare_filename_covered_by_basename():
    audit = audit_intent(
        _session(),
        [_node("user", "fix app.py")],
        [_state("t1", _edit("src/app.py"))],
    )
    assert audit.possibly_missed == []


def test_prompt_excerpt_and_node_id():
    audit = audit_intent(
        _session(),
        [_node("agent", "hi", 1), _node("user", "fix src/app.py", 2)],
        [_state("t1", _edit("src/app.py"))],
    )
    assert audit.prompt_node_id == 2
    assert "src/app.py" in audit.prompt_excerpt


def test_intent_dict_shape_and_heuristic_label():
    audit = audit_intent(
        _session(),
        [_node("user", "fix src/app.py")],
        [_state("t1", _edit("src/app.py"))],
    )
    d = intent_dict(audit)
    assert d["heuristic"] is True
    assert d["status"] == ALIGNED
    for key in ("referenced", "touched", "possibly_missed",
                "scope_drift", "notes"):
        assert key in d
    assert d["referenced"][0]["path"] == "src/app.py"


def test_render_intent_lines():
    audit = audit_intent(
        _session(),
        [_node("user", "fix src/app.py and docs/guide.md")],
        [_state("t1", _edit("src/app.py"))],
    )
    out = render_intent(audit)
    assert "FLAGGED" in out
    assert "[missed] docs/guide.md" in out
    skipped = render_intent(
        audit_intent(_session(), [_node("agent", "hi")], [])
    )
    assert skipped.startswith("SKIPPED")


# -- CLI ----------------------------------------------------------------------


def test_cli_aligned_exits_zero(intent_db, capsys):
    rc = main(["intent", "sess-aligned", "--sessions-db", str(intent_db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ALIGNED" in out


def test_cli_missed_exits_one(intent_db, capsys):
    rc = main(["intent", "sess-missed", "--sessions-db", str(intent_db)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "docs/guide.md" in out
    assert "missed" in out


def test_cli_drift_exits_one(intent_db, capsys):
    rc = main(["intent", "sess-drift", "--sessions-db", str(intent_db),
               "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["status"] == "flagged"
    assert payload["scope_drift"] == ["/repo/qa-pack/web/landing.html"]
    assert payload["possibly_missed"] == []
    assert payload["heuristic"] is True


def test_cli_no_paths_exits_one(intent_db, capsys):
    rc = main(["intent", "sess-nopaths", "--sessions-db", str(intent_db)])
    assert rc == 1
    assert "SKIPPED" in capsys.readouterr().out


def test_cli_no_user_message_skipped(intent_db, capsys):
    rc = main(["intent", "sess-nouser", "--sessions-db", str(intent_db),
               "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["status"] == "skipped"
    assert "user" in payload["reason"]


def test_cli_unknown_session_exits_two(intent_db, capsys):
    rc = main(["intent", "nope", "--sessions-db", str(intent_db)])
    assert rc == 2
    assert "nope" in capsys.readouterr().err


def test_cli_missing_db_exits_two(tmp_path):
    rc = main(["intent", "x", "--sessions-db", str(tmp_path / "gone.db")])
    assert rc == 2


def test_cli_prefix_resolves(intent_db, capsys):
    rc = main(["intent", "sess-ali", "--sessions-db", str(intent_db)])
    assert rc == 0


# -- session-end integration ---------------------------------------------------


def test_session_end_side_file_carries_intent(intent_db, tmp_path, capsys):
    out = tmp_path / "v.json"
    rc = main(["session-end", "--sessions-db", str(intent_db),
               "--session-id", "sess-missed", "--out", str(out)])
    capsys.readouterr()
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "intent" in payload
    intent = payload["intent"]
    assert intent["status"] == "flagged"
    assert intent["possibly_missed"] == ["docs/guide.md"]
    assert intent["heuristic"] is True


def test_session_end_intent_skipped_when_no_prompt(intent_db, tmp_path,
                                                   capsys):
    out = tmp_path / "v.json"
    rc = main(["session-end", "--sessions-db", str(intent_db),
               "--session-id", "sess-nouser", "--out", str(out)])
    capsys.readouterr()
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["intent"]["status"] == "skipped"
