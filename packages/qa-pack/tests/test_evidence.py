"""evidence/ — canonical model, providers, golden refactor parity and
the ``--surface auto`` smoke path. All stores are synthetic tmp trees."""

import json
import sqlite3

import pytest
from devin_internals.parsers import SessionsStore

from devin_qa_pack.cli import main
from devin_qa_pack.evidence import (
    EvidenceMessage,
    EvidenceToolCall,
    SessionEvidence,
    Surface,
    collect_evidences,
    connect_readonly,
    epoch_ms,
    match_evidence,
    normalize_role,
)
from devin_qa_pack.evidence.codex_cli import CodexCliProvider
from devin_qa_pack.evidence.copilot_cli import CopilotCliProvider
from devin_qa_pack.evidence.devin_cli import DevinCliProvider
from devin_qa_pack.evidence.vscode_chat import VscodeChatProvider
from devin_qa_pack.report import audit_evidence, audit_session

# -- canonical model -----------------------------------------------------------


def test_epoch_ms_normalizes_seconds_millis_iso_and_junk():
    assert epoch_ms(1_700_000_000) == 1_700_000_000_000.0
    assert epoch_ms(1_700_000_000_000) == 1_700_000_000_000.0
    assert epoch_ms("2023-11-14T22:13:20Z") == 1_700_000_000_000.0
    assert epoch_ms("not a time") is None
    assert epoch_ms(None) is None
    assert epoch_ms(-5) is None


def test_normalize_role_maps_surface_spellings():
    assert normalize_role("human") == "user"
    assert normalize_role("User") == "user"
    assert normalize_role("assistant") == "assistant"
    assert normalize_role("agent") == "assistant"  # devin fixtures role
    assert normalize_role("tool_call") == "tool"
    assert normalize_role("system") == "system"
    assert normalize_role(None) == "assistant"


def test_dataclasses_are_frozen():
    import dataclasses

    msg = EvidenceMessage(role="user", text="hi", ts_ms=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.text = "mutated"


def test_connect_readonly_opens_without_mutating(tmp_path):
    db = tmp_path / "ro.db"
    con = sqlite3.connect(db)
    with con:
        con.execute("CREATE TABLE t(x INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
    con.close()
    ro = connect_readonly(db)
    assert [r[0] for r in ro.execute("SELECT x FROM t")] == [1]
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO t VALUES (2)")
    ro.close()


def test_connect_readonly_missing_db_raises(tmp_path):
    with pytest.raises(sqlite3.Error):
        connect_readonly(tmp_path / "gone.db")


# -- devin-cli provider ---------------------------------------------------------


def test_devin_provider_reads_fixture_store(sessions_db):
    provider = DevinCliProvider(sessions_db)
    assert provider.available({})
    evidences = provider.sessions({})
    ids = {e.native_id for e in evidences}
    assert {"sess-verified", "sess-disputed"} <= ids
    assert all(e.surface == Surface.DEVIN_CLI for e in evidences)
    verified = next(e for e in evidences if e.native_id == "sess-verified")
    assert verified.working_dir is not None
    roles = [m.role for m in verified.messages]
    assert "user" in roles and "assistant" in roles
    assert all(isinstance(c, EvidenceToolCall) for c in verified.tool_calls)
    assert any(
        c.payload.get("kind") == "execute" for c in verified.tool_calls
    )


def test_devin_provider_decodes_undecodable_row_as_empty(sessions_db):
    """The sess-opaque NULL-payload row survives as an empty-payload call
    so verify sees unreadable ground truth, not a missing row."""
    provider = DevinCliProvider(sessions_db)
    opaque = next(
        e for e in provider.sessions({}) if e.native_id == "sess-opaque"
    )
    assert len(opaque.tool_calls) == 1
    assert opaque.tool_calls[0].payload == {}
    assert opaque.tool_calls[0].update_payload is None


def test_devin_provider_absent_store_is_unavailable(tmp_path):
    provider = DevinCliProvider(tmp_path / "gone.db")
    assert not provider.available({})
    assert provider.sessions({}) == []


# -- golden refactor parity ------------------------------------------------------


def test_golden_verdicts_match_legacy_store_path(sessions_db):
    """Refactor contract: the same sessions.db audited through the new
    evidence provider yields identical verdicts, claim kinds/details and
    statuses as the legacy store path — per session."""
    with SessionsStore(sessions_db) as store:
        legacy = {
            a.session_id: a
            for a in (
                audit_session(store, s) for s in store.sessions()
            )
        }
        provider = DevinCliProvider(sessions_db)
        evidence_audits = {}
        for ev in provider.sessions({}):
            session = next(s for s in store.sessions() if s.id == ev.native_id)
            evidence_audits[ev.native_id] = (
                audit_evidence(ev),
                audit_session(store, session),  # keep store open
            )
    assert set(evidence_audits) == set(legacy)
    for sid, (via_evidence, via_store) in evidence_audits.items():
        assert via_evidence.verdict == via_store.verdict == legacy[sid].verdict
        assert [
            (r.claim.kind, r.claim.detail, r.status)
            for r in via_evidence.results
        ] == [
            (r.claim.kind, r.claim.detail, r.status)
            for r in via_store.results
        ]
        assert via_evidence.session_id == f"devin-cli:{sid}"
        assert via_evidence.surface == "devin-cli"


# -- copilot-cli provider --------------------------------------------------------


def _copilot_session(root, sid, events, workspace_yaml=None, flat=False):
    if flat:
        path = root / "session-state" / f"{sid}.jsonl"
    else:
        path = root / "session-state" / sid / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    if workspace_yaml is not None:
        (path.parent / "workspace.yaml").write_text(
            workspace_yaml, encoding="utf-8"
        )
    return path


COPILOT_EVENTS = [
    {"id": "e1", "type": "session.start", "timestamp": "2026-01-01T10:00:00Z",
     "data": {}},
    {"id": "e2", "type": "user.message", "timestamp": "2026-01-01T10:00:01Z",
     "data": {"content": "fix the tests"}},
    {"id": "e3", "type": "assistant.message",
     "timestamp": "2026-01-01T10:00:02Z",
     "data": {"content": "All tests passed — pytest is green."}},
    {"id": "e4", "type": "tool.execution_start",
     "timestamp": "2026-01-01T10:00:03Z",
     "data": {"toolCallId": "call-1", "toolName": "shell",
              "command": "python -m pytest -q"}},
    {"id": "e5", "type": "tool.execution_complete",
     "timestamp": "2026-01-01T10:00:04Z",
     "data": {"toolCallId": "call-1", "success": True,
              "output": "12 passed"}},
]


def test_copilot_provider_reads_events_layout(tmp_path):
    root = tmp_path / "cop"
    _copilot_session(
        root, "11111111-2222-3333-4444-555555555555", COPILOT_EVENTS,
        workspace_yaml="git_root: /repo/proj\n",
    )
    provider = CopilotCliProvider([root])
    assert provider.available({})
    (ev,) = provider.sessions({})
    assert ev.surface == "copilot-cli"
    assert ev.native_id == "11111111-2222-3333-4444-555555555555"
    assert ev.working_dir == "/repo/proj"
    assert [m.role for m in ev.messages] == ["user", "assistant"]
    assert "pytest is green" in ev.messages[1].text
    (call,) = ev.tool_calls
    assert call.tool_call_id == "call-1"
    assert call.payload["command"] == "python -m pytest -q"
    # success bool → status string the verifier resolves
    assert call.update_payload["status"] == "completed"


def test_copilot_provider_flat_legacy_and_dedup(tmp_path):
    root = tmp_path / "cop"
    _copilot_session(root, "flat-id", COPILOT_EVENTS, flat=True)
    _copilot_session(root, "flat-id", COPILOT_EVENTS, flat=True,
                     )
    _copilot_session(root, "dir-id", COPILOT_EVENTS,
                     workspace_yaml="cwd: /repo/x\n")
    provider = CopilotCliProvider([root])
    ids = {e.native_id for e in provider.sessions({})}
    assert ids == {"flat-id", "dir-id"}
    # cwd scalar also accepted as the workspace seed
    assert next(e for e in provider.sessions({})
                if e.native_id == "dir-id").working_dir == "/repo/x"


def test_copilot_provider_audit_end_to_end(tmp_path):
    root = tmp_path / "cop"
    _copilot_session(root, "cop-sess", COPILOT_EVENTS)
    (ev,) = CopilotCliProvider([root]).sessions({})
    audit = audit_evidence(ev)
    assert audit.session_id == "copilot-cli:cop-sess"
    assert audit.verdict == "PASS"
    assert all(r.status == "verified" for r in audit.results)


def test_copilot_provider_malformed_lines_warn_not_fail(tmp_path):
    root = tmp_path / "cop"
    path = _copilot_session(root, "messy", COPILOT_EVENTS)
    with path.open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
    (ev,) = CopilotCliProvider([root]).sessions({})
    assert any("malformed" in w for w in ev.warnings)
    assert ev.messages  # the good lines still produced evidence


# -- codex-cli provider -----------------------------------------------------------


def _codex_rollout(root, sid, records, archived=False):
    path = (
        root / ("archived_sessions" if archived else "sessions")
        / "2026" / "01" / "rollout-2026-01-01T10-00-00-abc.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    return path


CODEX_RECORDS = [
    {"type": "session_meta", "timestamp": "2026-01-01T10:00:00Z",
     "payload": {"id": "codex-sess-1", "cwd": "/repo/proj",
                 "timestamp": "2026-01-01T10:00:00Z"}},
    {"type": "response_item", "timestamp": "2026-01-01T10:00:01Z",
     "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text",
                              "text": "run the suite"}]}},
    {"type": "response_item", "timestamp": "2026-01-01T10:00:02Z",
     "payload": {"type": "local_shell_call", "call_id": "lc-1",
                 "action": {"command": ["python", "-m", "pytest", "-q"]}}},
    {"type": "response_item", "timestamp": "2026-01-01T10:00:03Z",
     "payload": {"type": "local_shell_call_output", "call_id": "lc-1",
                 "output": json.dumps(
                     {"output": "12 passed",
                      "metadata": {"exit_code": 0}})}},
    {"type": "event_msg", "timestamp": "2026-01-01T10:00:04Z",
     "payload": {"type": "agent_message",
                 "message": "All tests passed under pytest."}},
]


def test_codex_provider_reads_rollout(tmp_path):
    root = tmp_path / "codex"
    _codex_rollout(root, "codex-sess-1", CODEX_RECORDS)
    provider = CodexCliProvider(root)
    assert provider.available({})
    (ev,) = provider.sessions({})
    assert ev.surface == "codex-cli"
    assert ev.native_id == "codex-sess-1"
    assert ev.working_dir == "/repo/proj"
    roles = [m.role for m in ev.messages]
    assert roles == ["user", "assistant"]
    (call,) = ev.tool_calls
    # argv list joined so the verifier digs a shell command
    assert call.payload["action"]["command"] == "python -m pytest -q"
    # decoded output: exit_code lifted to the top level
    assert call.update_payload["exit_code"] == 0
    assert "12 passed" in call.update_payload["output"]["output"]


def test_codex_provider_state_db_threads(tmp_path):
    root = tmp_path / "codex"
    root.mkdir()
    db = root / "state_5.sqlite"
    con = sqlite3.connect(db)
    with con:
        con.execute(
            "CREATE TABLE threads(id TEXT PRIMARY KEY, cwd TEXT,"
            " created_at INTEGER, first_user_message TEXT)"
        )
        con.execute(
            "INSERT INTO threads VALUES ('thread-1', '/repo/x',"
            " 1700000000000, 'please fix the bug')"
        )
    con.close()
    provider = CodexCliProvider(root)
    (ev,) = provider.sessions({})
    assert ev.native_id == "thread-1"
    assert ev.working_dir == "/repo/x"
    assert ev.messages[0].role == "user"
    # the store was opened read-only: contents unchanged
    ro = connect_readonly(db)
    assert ro.execute("SELECT COUNT(*) FROM threads").fetchone()[0] == 1
    ro.close()


def test_codex_thread_and_rollout_merge(tmp_path):
    root = tmp_path / "codex"
    root.mkdir()
    db = root / "state_3.sqlite"
    con = sqlite3.connect(db)
    with con:
        con.execute(
            "CREATE TABLE threads(id TEXT PRIMARY KEY, cwd TEXT,"
            " created_at INTEGER, first_user_message TEXT)"
        )
        con.execute(
            "INSERT INTO threads VALUES ('codex-sess-1', '/repo/x',"
            " 1700000000000, 'first line')"
        )
    con.close()
    _codex_rollout(root, "codex-sess-1", CODEX_RECORDS)
    (ev,) = CodexCliProvider(root).sessions({})
    # one session: rollout content kept, thread cwd filled
    assert ev.native_id == "codex-sess-1"
    assert ev.tool_calls  # rollout contributed calls


def test_codex_provider_audit_end_to_end(tmp_path):
    root = tmp_path / "codex"
    _codex_rollout(root, "codex-pass", CODEX_RECORDS)
    (ev,) = CodexCliProvider(root).sessions({})
    audit = audit_evidence(ev)
    assert audit.session_id == "codex-cli:codex-sess-1"
    assert audit.verdict == "PASS"


# -- vscode-chat provider ---------------------------------------------------------


def _vscode_layout(tmp_path):
    user = tmp_path / "Code" / "User"
    ws = user / "workspaceStorage" / "%2Frepo%2Fproj"
    ws.mkdir(parents=True)
    return user, ws


def test_vscode_provider_reads_json_snapshot(tmp_path):
    user, ws = _vscode_layout(tmp_path)
    chat_dir = ws / "chatSessions"
    chat_dir.mkdir()
    (chat_dir / "chat-1.json").write_text(json.dumps({
        "sessionId": "chat-1",
        "creationDate": 1700000000000,
        "requests": [
            {"message": {"text": "fix the tests"},
             "timestamp": 1700000001000,
             "response": [
                 {"kind": "toolInvocation", "toolCallId": "t1",
                  "toolName": "runInTerminal",
                  "command": "python -m pytest -q",
                  "toolSpecificData": {
                      "kind": "terminal",
                      "terminalCommandState": {"exitCode": 0}}},
                 {"value": "All tests passed — pytest is green."},
             ],
             "responseTimestamp": 1700000002000},
        ],
    }), encoding="utf-8")
    provider = VscodeChatProvider([user])
    assert provider.available({})
    (ev,) = provider.sessions({})
    assert ev.surface == "vscode-chat"
    assert ev.native_id == "chat-1"
    assert ev.working_dir == "/repo/proj"  # URL-decoded wsDir
    assert [m.role for m in ev.messages] == ["user", "assistant"]
    (call,) = ev.tool_calls
    assert call.tool_call_id == "t1"
    assert call.payload["command"] == "python -m pytest -q"


def test_vscode_workspace_json_folder_wins(tmp_path):
    user, ws = _vscode_layout(tmp_path)
    (ws / "workspace.json").write_text(
        json.dumps({"folder": "file:///real/path/proj"}), encoding="utf-8"
    )
    chat_dir = ws / "chatSessions"
    chat_dir.mkdir()
    (chat_dir / "c.json").write_text(
        json.dumps({"requests": []}), encoding="utf-8"
    )
    (ev,) = VscodeChatProvider([user]).sessions({})
    assert ev.working_dir == "/real/path/proj"


def test_vscode_file_uri_windows_drive_strips_uri_slash(tmp_path):
    user, ws = _vscode_layout(tmp_path)
    (ws / "workspace.json").write_text(
        json.dumps({"folder": "file:///C:/repo/proj"}), encoding="utf-8"
    )
    chat_dir = ws / "chatSessions"
    chat_dir.mkdir()
    (chat_dir / "c.json").write_text(
        json.dumps({"requests": []}), encoding="utf-8"
    )
    (ev,) = VscodeChatProvider([user]).sessions({})
    assert ev.working_dir == "C:/repo/proj"  # '/C:/' is a URI artifact, not a path


def test_vscode_jsonl_mutation_log_replays_and_wins(tmp_path):
    user, ws = _vscode_layout(tmp_path)
    chat_dir = ws / "chatSessions"
    chat_dir.mkdir()
    # Same id: .json says "old", .jsonl mutates to a session with a real
    # request — the mutation log wins and the log itself replays.
    (chat_dir / "chat-2.json").write_text(
        json.dumps({"requests": [{"message": {"text": "old"}}]}),
        encoding="utf-8",
    )
    (chat_dir / "chat-2.jsonl").write_text("\n".join([
        json.dumps({"kind": 0, "v": {"requests": []}}),
        json.dumps({"kind": 2, "k": ["requests"], "i": 0,
                    "v": [{"message": {"text": "new prompt"},
                           "response": [{"value": "did it"}]}]}),
        json.dumps({"kind": 1, "k": ["customTitle"], "v": "T"}),
    ]), encoding="utf-8")
    (ev,) = VscodeChatProvider([user]).sessions({})
    assert ev.native_id == "chat-2"
    texts = [m.text for m in ev.messages]
    assert "new prompt" in texts and "old" not in texts


def test_vscode_global_storage_scanned(tmp_path):
    user = tmp_path / "Code" / "User"
    gdir = user / "globalStorage" / "emptyWindowChatSessions"
    gdir.mkdir(parents=True)
    (gdir / "g.json").write_text(json.dumps({
        "requests": [{"message": {"text": "hi"},
                      "response": [{"value": "hello"}]}],
    }), encoding="utf-8")
    (ev,) = VscodeChatProvider([user]).sessions({})
    assert ev.native_id == "g"
    assert ev.working_dir is None  # globalStorage has no workspace


def test_vscode_provider_audit_end_to_end(tmp_path):
    user, ws = _vscode_layout(tmp_path)
    chat_dir = ws / "chatSessions"
    chat_dir.mkdir()
    (chat_dir / "chat-3.json").write_text(json.dumps({
        "requests": [
            {"message": {"text": "run pytest"},
             "response": [
                 {"kind": "toolInvocation", "toolCallId": "t1",
                  "command": "python -m pytest -q",
                  "isComplete": True,
                  "resultDetails": {"output": "12 passed"},
                  "toolSpecificData": {
                      "kind": "terminal",
                      "terminalCommandState": {"exitCode": 0}}},
                 {"value": "All tests passed — pytest is green."},
             ]},
        ],
    }), encoding="utf-8")
    (ev,) = VscodeChatProvider([user]).sessions({})
    # spec §4/§6.5: inline outcome fields live in update_payload, the
    # canonical home of exit status — payload keeps the raw invocation.
    (call,) = ev.tool_calls
    assert call.update_payload["exit_code"] == 0
    assert "exit_code" not in call.payload
    # inline outcome fields follow the same rule (spec §4)
    assert call.update_payload["isComplete"] is True
    assert call.update_payload["resultDetails"] == {"output": "12 passed"}
    audit = audit_evidence(ev)
    assert audit.session_id == "vscode-chat:chat-3"
    assert audit.verdict == "PASS"


# -- cross-surface collection + matching -------------------------------------------


def _isolated_home(monkeypatch, tmp_path):
    """Point every auto-detection root at empty tmp dirs so only the
    stores a test creates are visible."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("COPILOT_DIR", raising=False)
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_SESSIONS_DIR", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    return home


def test_collect_evidences_skips_absent_surfaces(monkeypatch, tmp_path,
                                               sessions_db):
    _isolated_home(monkeypatch, tmp_path)
    evidences, warnings = collect_evidences(
        Surface.AUTO, sessions_db=sessions_db
    )
    assert warnings == []
    assert evidences  # devin sessions from the pinned db
    assert all(e.surface == "devin-cli" for e in evidences)


def test_collect_evidences_two_surfaces(monkeypatch, tmp_path, sessions_db):
    _isolated_home(monkeypatch, tmp_path)
    cop = tmp_path / "cop"
    _copilot_session(cop, "cop-1", COPILOT_EVENTS)
    monkeypatch.setenv("COPILOT_DIR", str(cop))
    evidences, warnings = collect_evidences(
        Surface.AUTO, sessions_db=sessions_db
    )
    assert warnings == []
    surfaces = {e.surface for e in evidences}
    assert surfaces == {"devin-cli", "copilot-cli"}


def test_match_evidence_exact_prefix_and_surface_scope():
    ev_a = SessionEvidence(surface="devin-cli", native_id="abc123",
                           ref="", started_at=None, working_dir=None)
    ev_b = SessionEvidence(surface="copilot-cli", native_id="abc999",
                           ref="", started_at=None, working_dir=None)
    pool = [ev_a, ev_b]
    assert match_evidence(pool, "abc123") is ev_a
    assert match_evidence(pool, "devin-cli:abc123") is ev_a
    assert match_evidence(pool, "copilot-cli:abc") is ev_b
    ambiguous = match_evidence(pool, "abc")
    assert isinstance(ambiguous, list)
    assert len(ambiguous) == 2 and ev_a in ambiguous and ev_b in ambiguous
    assert match_evidence(pool, "devin-cli:abc") is ev_a
    assert match_evidence(pool, "nope") is None


def test_match_evidence_same_native_id_on_two_surfaces_is_ambiguous():
    """An exact native-id hit on several surfaces must not silently pick
    the first — the caller lists the candidates."""
    ev_a = SessionEvidence(surface="devin-cli", native_id="dup",
                           ref="", started_at=None, working_dir=None)
    ev_b = SessionEvidence(surface="codex-cli", native_id="dup",
                           ref="", started_at=None, working_dir=None)
    found = match_evidence([ev_a, ev_b], "dup")
    assert isinstance(found, list)
    assert len(found) == 2 and ev_a in found and ev_b in found
    # a surface-scoped ref still resolves uniquely
    assert match_evidence([ev_a, ev_b], "codex-cli:dup") is ev_b


def test_match_evidence_same_surface_dup_resolves_by_ref():
    """Same native id on one surface twice (copilot keeps an id across
    workspaces): scoped surface:id stays ambiguous in candidates, but a
    canonical-ref (or unique ref-prefix) query singles one out — the ref
    embeds the workspace hash."""
    ev_a = SessionEvidence(surface="copilot-cli", native_id="dup",
                           ref="copilot-cli:ws-aaa:native:dup",
                           started_at=None, working_dir=None)
    ev_b = SessionEvidence(surface="copilot-cli", native_id="dup",
                           ref="copilot-cli:ws-bbb:native:dup",
                           started_at=None, working_dir=None)
    pool = [ev_a, ev_b]
    assert isinstance(match_evidence(pool, "copilot-cli:dup"), list)
    assert match_evidence(pool, "copilot-cli:ws-aaa:native:dup") is ev_a
    assert match_evidence(pool, "copilot-cli:ws-bbb") is ev_b


# -- smoke: audit --surface auto ---------------------------------------------------


def test_cli_surface_auto_smoke(monkeypatch, tmp_path, sessions_db, capsys):
    """``audit --surface auto`` over a tree holding two surfaces' stores:
    devin sessions (pinned db) + one copilot session — ids come back
    namespaced, no surface errors out."""
    _isolated_home(monkeypatch, tmp_path)
    cop = tmp_path / "cop"
    _copilot_session(cop, "cop-smoke", COPILOT_EVENTS)
    monkeypatch.setenv("COPILOT_DIR", str(cop))

    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--surface", "auto", "--all", "--json"])
    payload = json.loads(capsys.readouterr().out)
    ids = {s["session_id"] for s in payload["sessions"]}
    assert "copilot-cli:cop-smoke" in ids
    assert "devin-cli:sess-verified" in ids
    assert all(":" in sid for sid in ids)
    surfaces = {s["surface"] for s in payload["sessions"]}
    assert surfaces == {"devin-cli", "copilot-cli"}
    assert rc in (0, 1)


def test_cli_surface_auto_session_query(monkeypatch, tmp_path, sessions_db,
                                        capsys):
    _isolated_home(monkeypatch, tmp_path)
    cop = tmp_path / "cop"
    _copilot_session(cop, "cop-target", COPILOT_EVENTS)
    monkeypatch.setenv("COPILOT_DIR", str(cop))

    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--surface", "auto", "--session", "cop-target", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    (entry,) = payload["sessions"]
    assert entry["session_id"] == "copilot-cli:cop-target"
    assert entry["verdict"] == "PASS"


def test_cli_surface_scoped(monkeypatch, tmp_path, sessions_db, capsys):
    _isolated_home(monkeypatch, tmp_path)
    cop = tmp_path / "cop"
    _copilot_session(cop, "cop-only", COPILOT_EVENTS)
    monkeypatch.setenv("COPILOT_DIR", str(cop))
    rc = main(["audit", "--surface", "copilot-cli", "--all", "--json"])
    payload = json.loads(capsys.readouterr().out)
    ids = {s["session_id"] for s in payload["sessions"]}
    assert ids == {"copilot-cli:cop-only"}
    assert rc == 0


def test_cli_ambiguous_prefix_across_surfaces(monkeypatch, tmp_path,
                                              sessions_db, capsys):
    _isolated_home(monkeypatch, tmp_path)
    cop = tmp_path / "cop"
    _copilot_session(cop, "sess-cop", COPILOT_EVENTS)
    monkeypatch.setenv("COPILOT_DIR", str(cop))
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--surface", "auto", "--session", "sess"])
    assert rc == 2
    assert "ambiguous" in capsys.readouterr().err


def test_mcp_do_audit_surface(monkeypatch, tmp_path, sessions_db):
    _isolated_home(monkeypatch, tmp_path)
    cop = tmp_path / "cop"
    _copilot_session(cop, "cop-mcp", COPILOT_EVENTS)
    monkeypatch.setenv("COPILOT_DIR", str(cop))
    from devin_qa_pack.mcp_server import do_audit

    out = do_audit(audit_all_sessions=True, surface="copilot-cli")
    ids = {s["session_id"] for s in out["sessions"]}
    assert ids == {"copilot-cli:cop-mcp"}
    out = do_audit(session_id="cop-mcp", surface="copilot-cli")
    assert out["sessions"][0]["verdict"] == "PASS"
    bad = do_audit(surface="not-a-surface")
    assert bad["error"] == "usage"


# -- review fixes ------------------------------------------------------------


def test_filter_evidences_sorts_globally_newest_before_limit():
    """Cross-surface ``--limit`` must see the globally newest session —
    an older PASS in the first provider's list must not mask a newer
    PARTIAL on another surface."""
    from devin_qa_pack.report import filter_evidences

    old = SessionEvidence(
        surface="devin-cli", native_id="old", ref="",
        started_at="2026-01-01T00:00:00.000Z", working_dir=None,
    )
    newest = SessionEvidence(
        surface="copilot-cli", native_id="new", ref="",
        started_at="2026-01-03T00:00:00.000Z", working_dir=None,
    )
    no_ts = SessionEvidence(
        surface="codex-cli", native_id="no-ts", ref="",
        started_at=None, working_dir=None,
    )
    # devin provider order comes first in the merged list
    out = filter_evidences([old, newest, no_ts], limit=2)
    assert [e.native_id for e in out] == ["new", "old"]


def test_codex_output_textual_exit_code(tmp_path):
    """``function_call_output``/shell outputs carrying a plain-text exit
    code normalize into a verifiable status."""
    root = tmp_path / "codex"
    records = [
        {"type": "session_meta", "payload": {"id": "s1",
                                             "cwd": "/repo/x"}},
        {"type": "response_item",
         "payload": {"type": "local_shell_call", "call_id": "c1",
                     "action": {"command": ["pytest", "-q"]}}},
        {"type": "response_item",
         "payload": {"type": "local_shell_call_output", "call_id": "c1",
                     "output": "12 passed\nExit code: 0"}},
        {"type": "event_msg",
         "payload": {"type": "agent_message",
                     "message": "All tests passed under pytest."}},
    ]
    _codex_rollout(root, "s1", records)
    (ev,) = CodexCliProvider(root).sessions({})
    (call,) = ev.tool_calls
    assert call.update_payload["exit_code"] == 0
    audit = audit_evidence(ev)
    assert audit.verdict == "PASS"


def test_codex_output_bare_integer_is_not_status(tmp_path):
    """``"1"`` alone is stdout, not a status envelope — a bare integer
    must never fabricate an exit code."""
    root = tmp_path / "codex"
    records = [
        {"type": "session_meta", "payload": {"id": "s2"}},
        {"type": "response_item",
         "payload": {"type": "local_shell_call", "call_id": "c1",
                     "action": {"command": ["pytest"]}}},
        {"type": "response_item",
         "payload": {"type": "local_shell_call_output", "call_id": "c1",
                     "output": "1"}},
    ]
    _codex_rollout(root, "s2", records)
    (ev,) = CodexCliProvider(root).sessions({})
    assert "exit_code" not in ev.tool_calls[0].update_payload


def test_codex_output_embedded_exit_code_is_not_status(tmp_path):
    """Stdout that merely *contains* an exit-code line (mid-output, more
    output after) is not a status — a failed run must not verify."""
    root = tmp_path / "codex"
    records = [
        {"type": "session_meta", "payload": {"id": "s3"}},
        {"type": "response_item",
         "payload": {"type": "local_shell_call", "call_id": "c1",
                     "action": {"command": ["grep", "-c", "ok", "log"]}}},
        {"type": "response_item",
         "payload": {"type": "local_shell_call_output", "call_id": "c1",
                     "output": "Exit code: 0\n3\nreal tail"}},
    ]
    _codex_rollout(root, "s3", records)
    (ev,) = CodexCliProvider(root).sessions({})
    assert "exit_code" not in ev.tool_calls[0].update_payload


def test_vscode_hashed_workspace_id_yields_no_working_dir(tmp_path):
    """A hashed workspaceStorage id without workspace.json must not
    fabricate a working_dir — it would break --within and file checks."""
    user = tmp_path / "Code" / "User"
    ws = user / "workspaceStorage" / "e9f4a1b2c3d4e5f6a7b8c9d0e1f2a3b4"
    chat_dir = ws / "chatSessions"
    chat_dir.mkdir(parents=True)
    (chat_dir / "h.json").write_text(json.dumps({
        "requests": [{"message": {"text": "hi"},
                      "response": [{"value": "hello"}]}],
    }), encoding="utf-8")
    (ev,) = VscodeChatProvider([user]).sessions({})
    assert ev.working_dir is None
    assert any("no resolvable folder path" in w for w in ev.warnings)


def test_vscode_decoded_path_like_ws_dir_still_works(tmp_path):
    user, ws = _vscode_layout(tmp_path)  # "%2Frepo%2Fproj" decodes to a path
    chat_dir = ws / "chatSessions"
    chat_dir.mkdir()
    (chat_dir / "p.json").write_text(json.dumps({"requests": []}),
                                     encoding="utf-8")
    (ev,) = VscodeChatProvider([user]).sessions({})
    assert ev.working_dir == "/repo/proj"
    assert ev.warnings == []


def test_mcp_do_audit_no_sessions_fails_closed(monkeypatch, tmp_path):
    """Provider-mode do_audit mirrors the CLI: zero sessions matched is
    an error, not an empty success."""
    _isolated_home(monkeypatch, tmp_path)
    from devin_qa_pack.mcp_server import do_audit

    out = do_audit(audit_all_sessions=True, surface="auto")
    assert out["error"] == "no_sessions"


def test_claim_node_id_cites_source_id(sessions_db):
    """devin-cli populates source_id from message_nodes.node_id so audit
    JSON points at the real node, not the positional index."""
    import sqlite3

    from conftest import add_message, add_session

    from devin_qa_pack.claims import extract_claims

    con = sqlite3.connect(sessions_db)
    with con:
        add_session(con, "sess-sparse", "/repo/x", "Sparse")
        # node ids deliberately non-contiguous with positions
        add_message(con, "sess-sparse", 10, "agent", "Tests passed.")
    con.close()
    provider = DevinCliProvider(sessions_db)
    ev = next(e for e in provider.sessions({})
              if e.native_id == "sess-sparse")
    assert ev.messages[0].source_id == 10
    claims = extract_claims(ev.messages, "sess-sparse")
    assert claims[0].node_id == 10


def test_claim_node_id_falls_back_to_position():
    from devin_qa_pack.claims import extract_claims

    msgs = [
        EvidenceMessage(role="user", text="do it", ts_ms=None),
        EvidenceMessage(role="assistant", text="Tests passed.",
                        ts_ms=None),
    ]
    claims = extract_claims(msgs, "s")
    assert claims[0].node_id == 2
