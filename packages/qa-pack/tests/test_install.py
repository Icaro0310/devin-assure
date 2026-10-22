"""install subcommand + the scheduled audit sweep.

``devin-qa-pack install --daily`` registers a daily ``audit --all``
sweep through the shared ``devin-install-scheduler`` backends (cron /
Task Scheduler / elapsed registry). The registered command is the
scheduled sibling of ``session-end``: read-only on every store, writing
only under the data dir's ``qa/`` folder — the sweep payload plus
``verdicts.jsonl`` ledger lines. Isolation rule: no test here opens a
real store — everything runs on synthetic fixtures under tmp dirs with
the environment scrubbed.
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import devin_install_scheduler.install as _sched
import pytest
from conftest import add_message, add_session, add_tool_call, exec_call
from devin_internals.fixtures import create_sessions_db
from devin_qa_pack import install
from devin_qa_pack.cli import main
from devin_qa_pack.sweep import SWEEP_ORIGIN

JOB = "devin-qa-pack-audit-daily"


def _registry(config_dir: Path) -> dict:
    return json.loads(
        (config_dir / ".devin-ecosystem" / "scheduled.json").read_text(
            encoding="utf-8"
        )
    )


def _scrub_surfaces(monkeypatch, tmp_path: Path, data_dir: Path) -> None:
    """Confine ``--surface auto`` discovery to the fixture data dir.

    HOME moves under tmp_path so copilot/codex/vscode roots
    (``~/.copilot``, ``~/.codex``, ``~/.config/Code/...``) resolve to
    nothing; their env overrides are dropped; DEVIN_DATA_DIR pins the
    devin-cli store. No real store is ever touched.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("COPILOT_DIR", "COPILOT_HOME", "CODEX_HOME",
                "CODEX_SESSIONS_DIR", "APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("DEVIN_DATA_DIR", str(data_dir))


def _data_dir_store(data_dir: Path) -> Path:
    """A minimal synthetic sessions.db at <data_dir>/cli/sessions.db."""
    db = create_sessions_db(data_dir / "cli" / "sessions.db",
                            n_sessions=0)
    con = sqlite3.connect(db)
    with con:
        add_session(con, "sweep-verified", str(data_dir / "wd"),
                    "Sweep session")
        add_message(con, "sweep-verified", 1, "user", "run the tests")
        add_message(con, "sweep-verified", 2, "agent",
                    "All tests passed — pytest is green.")
        add_tool_call(con, "sweep-verified", "tc-pytest",
                      exec_call("python -m pytest -q", output="3 passed"))
    con.close()
    return db


# --------------------------------------------------------------- install --


def test_install_elapsed_writes_registry(tmp_path):
    res = install.install_daily(config_dir=str(tmp_path / "cfg"),
                                backend="elapsed")
    assert res["backend"] == "elapsed"
    assert res["job"] == JOB
    reg = _registry(tmp_path / "cfg")
    job = reg["jobs"][JOB]
    cmd = job["command"]
    assert f'"{sys.executable}"' in cmd
    assert "-m devin_qa_pack.cli audit --all" in cmd
    assert "--surface auto" in cmd
    # store + output are baked to absolute paths — the job must not
    # depend on scheduler-time env (cron's bare HOME, schtasks).
    assert "--sessions-db" in cmd
    assert "cli/sessions.db" in cmd.replace("\\", "/")
    assert "--out" in cmd
    assert "qa/audit-sweep.json" in cmd.replace("\\", "/")
    # the read-only sweep is all that is ever scheduled
    assert "--apply" not in cmd and "session-end" not in cmd
    assert job["guarded_command"] == job["command"]
    assert job["interval_h"] == 24
    assert job["requires_devin_closed"] is False
    assert job["installed_at"] > 0 and job["last_run"] == 0


def test_install_data_dir_lands_in_command(tmp_path):
    res = install.install_daily(data_dir=str(tmp_path / "dd"),
                                config_dir=str(tmp_path / "cfg"),
                                backend="elapsed")
    job = _registry(tmp_path / "cfg")["jobs"][JOB]
    assert str(tmp_path / "dd" / "qa" / "audit-sweep.json") in job["command"]
    assert str(tmp_path / "dd" / "cli" / "sessions.db") in job["command"]
    assert res["command"] == job["command"]


def test_install_surface_override(tmp_path):
    install.install_daily(surface="devin-cli",
                          config_dir=str(tmp_path / "cfg"),
                          backend="elapsed")
    job = _registry(tmp_path / "cfg")["jobs"][JOB]
    assert "--surface devin-cli" in job["command"]
    assert "--surface auto" not in job["command"]


def test_install_bad_backend(tmp_path):
    with pytest.raises(ValueError):
        install.install_daily(config_dir=str(tmp_path), backend="bogus")


def test_install_cron_tagged_line(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(_sched.shutil, "which", lambda _c: "/usr/bin/crontab")

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(_sched.subprocess, "run", fake_run)
    res = install.install_daily(config_dir=str(tmp_path / "cfg"),
                                backend="cron")
    assert res["backend"] == "cron"
    stdin = calls[-1][1]["input"]
    assert "@daily" in stdin
    assert f"# devin-ecosystem:{JOB}" in stdin
    assert "audit-sweep.json" in stdin


def test_install_cli(tmp_path, capsys):
    rc = main(["install", "--daily", "--backend", "elapsed",
               "--config-dir", str(tmp_path / "cfg")])
    assert rc == 0
    out = capsys.readouterr().out
    assert JOB in out and "elapsed" in out
    assert "-m devin_qa_pack.cli audit --all" in out

    rc = main(["install", "--daily", "--backend", "elapsed",
               "--config-dir", str(tmp_path / "cfg2"), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["job"] == JOB
    assert payload["backend"] == "elapsed"


def test_install_requires_daily_flag(tmp_path, capsys):
    rc = main(["install", "--backend", "elapsed",
               "--config-dir", str(tmp_path)])
    assert rc == 2
    assert "--daily" in capsys.readouterr().err
    assert not (tmp_path / ".devin-ecosystem").exists()


# ------------------------------------------------------------- audit --out


def test_audit_out_writes_payload_and_ledger(sessions_db, tmp_path, capsys):
    out = tmp_path / "qa" / "audit-sweep.json"
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--all", "--out", str(out)])
    capsys.readouterr()
    assert rc == 1  # fixture mixes PASS/PARTIAL/UNVERIFIED
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["sessions"]) == 8
    assert payload["audited_at"]
    assert payload["version"]
    lines = (tmp_path / "qa" / "verdicts.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 8
    rec = json.loads(lines[0])
    for key in ("session_id", "verdict", "audited_at", "claims",
                "verified", "disputed", "unverifiable", "origin"):
        assert key in rec
    assert rec["origin"] == SWEEP_ORIGIN == "sweep"


def test_audit_out_single_session(sessions_db, tmp_path, capsys):
    out = tmp_path / "deep" / "nested" / "one.json"
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--session", "sess-verified", "--out", str(out)])
    capsys.readouterr()
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    (entry,) = payload["sessions"]
    assert entry["session_id"] == "sess-verified"
    lines = (tmp_path / "deep" / "nested" / "verdicts.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_audit_out_never_touches_store(sessions_db, tmp_path, capsys):
    """The store is untouched; output lands only under --out's root."""
    before = sessions_db.stat().st_mtime_ns
    listing_before = sorted(p.name for p in sessions_db.parent.iterdir())
    out = tmp_path / "elsewhere" / "qa" / "audit-sweep.json"
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--all", "--out", str(out)])
    capsys.readouterr()
    assert rc == 1
    assert sessions_db.stat().st_mtime_ns == before
    assert sorted(p.name for p in sessions_db.parent.iterdir()) == sorted(
        listing_before + ["elsewhere"])
    assert not (sessions_db.parent / "qa").exists()


def test_audit_out_unwritable_path_exits_two(sessions_db, tmp_path, capsys):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a dir", encoding="utf-8")
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--all", "--out", str(blocker / "deep" / "o.json")])
    assert rc == 2
    assert "cannot write" in capsys.readouterr().err


def test_audit_out_named_verdicts_jsonl_stays_valid_json(
        sessions_db, tmp_path, capsys):
    """--out pointing at verdicts.jsonl itself must not corrupt the
    payload by appending a ledger line to it."""
    out = tmp_path / "qa" / "verdicts.jsonl"
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--session", "sess-verified", "--out", str(out)])
    capsys.readouterr()
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["sessions"][0]["verdict"] == "PASS"


# -------------------------------------------- the registered command shape


def test_scheduled_sweep_shape_end_to_end(tmp_path, monkeypatch, capsys):
    """The invocation ``install`` registers — ``audit --all --surface
    auto --sessions-db <db> --out <data>/qa/audit-sweep.json`` — produces
    artifacts under the data dir only, with every other surface's env
    scrubbed so no real store can be discovered."""
    data_dir = tmp_path / "devin-data"
    db = _data_dir_store(data_dir)
    _scrub_surfaces(monkeypatch, tmp_path, data_dir)

    res = install.install_daily(data_dir=str(data_dir),
                                config_dir=str(tmp_path / "cfg"),
                                backend="elapsed")
    cmd = res["command"]
    assert "--surface auto" in cmd
    assert str(data_dir / "cli" / "sessions.db") in cmd
    out_path = Path(cmd.rsplit('--out "', 1)[1].rstrip('"'))
    assert out_path == data_dir / "qa" / "audit-sweep.json"

    db_mtime = db.stat().st_mtime_ns
    # the exact invocation the registry stores — pinned store included
    rc = main(["audit", "--all", "--surface", "auto",
               "--sessions-db", str(db),
               "--out", str(out_path)])
    assert rc == 0  # the one fixture session is fully PASS
    text = capsys.readouterr().out
    assert "devin-cli:sweep-verified" in text

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    (entry,) = payload["sessions"]
    assert entry["session_id"] == "devin-cli:sweep-verified"
    assert entry["verdict"] == "PASS"
    assert entry["surface"] == "devin-cli"
    rec = json.loads(
        (data_dir / "qa" / "verdicts.jsonl")
        .read_text(encoding="utf-8").splitlines()[0])
    assert rec["origin"] == "sweep" and rec["surface"] == "devin-cli"

    # isolation: the sweep wrote nowhere but <data>/qa/
    assert db.stat().st_mtime_ns == db_mtime
    assert sorted(p.name for p in data_dir.iterdir()) == ["cli", "qa"]


def test_scheduled_sweep_no_stores_exits_two_writes_nothing(
        tmp_path, monkeypatch, capsys):
    """On a machine with no agent stores the scheduled run fails closed
    (exit 2) and writes nothing — no payload, no ledger."""
    _scrub_surfaces(monkeypatch, tmp_path, tmp_path / "empty-data")
    out = tmp_path / "qa" / "audit-sweep.json"
    rc = main(["audit", "--all", "--surface", "auto", "--out", str(out)])
    assert rc == 2
    assert "no sessions to audit" in capsys.readouterr().err
    assert not out.exists()
    assert not (tmp_path / "qa" / "verdicts.jsonl").exists()


def test_audit_out_existing_verdicts_ledger_is_never_clobbered(
        sessions_db, tmp_path, capsys):
    """--out naming a populated verdicts.jsonl must refuse BEFORE the
    payload write — the pre-existing ledger survives byte-for-byte."""
    out = tmp_path / "qa" / "verdicts.jsonl"
    out.parent.mkdir(parents=True)
    out.write_text('{"verdict":"PASS"}\n', encoding="utf-8")
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--session", "sess-verified", "--out", str(out)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "verdict ledger" in err
    assert out.read_text(encoding="utf-8") == '{"verdict":"PASS"}\n'


def test_write_json_never_shares_a_stale_tmp(sessions_db, tmp_path,
                                             capsys):
    """A leftover <name>.tmp from a crashed/concurrent run is neither
    consumed nor overwritten — temp names are unique per writer."""
    out = tmp_path / "qa" / "audit-sweep.json"
    out.parent.mkdir(parents=True)
    stale = out.with_name(out.name + ".tmp")
    stale.write_text("SENTINEL", encoding="utf-8")
    rc = main(["audit", "--sessions-db", str(sessions_db),
               "--session", "sess-verified", "--out", str(out)])
    capsys.readouterr()
    assert rc == 0
    assert json.loads(out.read_text(encoding="utf-8"))["sessions"]
    assert stale.read_text(encoding="utf-8") == "SENTINEL"


def test_install_data_dir_shell_metachars_rejected(tmp_path):
    """A --data-dir that would inject into cron's /bin/sh (or cmd's
    %VAR%) must be refused before any job is registered."""
    with pytest.raises(ValueError):
        install.install_daily(
            data_dir=str(tmp_path / 'evil"$(touch /tmp/pwned)"'),
            config_dir=str(tmp_path / "cfg"),
            backend="elapsed",
        )
    assert not (tmp_path / "cfg").exists() or "jobs" not in _registry(
        tmp_path / "cfg")
