"""Composite action contract for the root ``action.yml`` ("devin-qa-pack
audit"): pins the gate semantics the published action must keep.

- the pack is installed from ``github.action_path`` (the revision under
  test), never an unpinned PyPI release and never a ``git+`` URL keyed
  on ``github.sha`` — inside a composite action ``github.sha`` is the
  *caller* repo's commit, which does not exist in this repo;
- inputs reach bash only through env vars, never ``${{ inputs.* }}``
  inside ``run:`` blocks;
- every declared input is consumed somewhere (env or ``with:``) — no
  phantom wiring;
- the run step forwards the flags the CLI actually declares
  (``--session`` / ``--all`` / ``--limit`` / ``--sessions-db``), quoted
  so db paths with spaces survive word-splitting;
- ``audit`` is a gate, not informational: exit 2 when nothing could be
  audited, exit 1 when any audited session is not PASS, 0 only when
  every audited session passed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ACTION = Path(__file__).resolve().parents[3] / "action.yml"
BASH = shutil.which("bash")


def _load() -> dict:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _load()["runs"]["steps"]


def _run_blocks() -> list[str]:
    return [s["run"] for s in _steps() if "run" in s]


def _step_named(fragment: str) -> dict:
    for step in _steps():
        if fragment in step.get("name", ""):
            return step
    raise AssertionError(f"no step named like {fragment!r}")


def _audit_args(*argv: str):
    from devin_qa_pack import cli

    return cli._build_parser().parse_args(["audit", *argv])


def test_action_is_composite() -> None:
    assert _load()["runs"]["using"] == "composite"


def test_install_uses_action_source_not_registry() -> None:
    """The pack under test is the checked-out action source.

    ``pip install devin-qa-pack`` would grade whatever PyPI happens to
    serve; ``git+https://…@${{ github.sha }}`` resolves the *caller*
    repo's commit inside a composite action and fails for any external
    consumer. The install must go through an env var bound to
    ``github.action_path`` — the same pattern the evals action uses.
    """
    text = ACTION.read_text(encoding="utf-8")
    assert "github.action_path" in text
    assert "pip install devin-qa-pack" not in text
    assert "git+https://github.com/Icaro0310/devin-assure" not in text

    install = _step_named("Install")
    env = install.get("env", {})
    action_path_vars = [k for k, v in env.items() if v == "${{ github.action_path }}"]
    assert action_path_vars, "install step must bind github.action_path to env"
    run = install["run"]
    assert "pip install" in run
    assert any(f"${v}" in run or f"${{{v}}}" in run for v in action_path_vars)
    assert "qa-pack" in run


def test_inputs_not_interpolated_in_run_blocks() -> None:
    """Inputs reach bash only via env vars — ``${{ inputs.* }}`` inside
    ``run:`` is a script-injection path on caller-controlled values."""
    for block in _run_blocks():
        assert "${{ inputs." not in block


def test_every_declared_input_is_consumed() -> None:
    """Every key under ``inputs:`` is referenced at least once — a
    declared input nobody reads is dead contract surface."""
    declared = set(_load()["inputs"])
    referenced = set(
        re.findall(
            r"\$\{\{ inputs\.([A-Za-z0-9_-]+)", ACTION.read_text(encoding="utf-8")
        )
    )
    assert declared <= referenced


def test_audit_step_maps_inputs_to_env() -> None:
    env = _step_named("Run audit")["env"]
    assert env["SESSIONS_DB"] == "${{ inputs.sessions-db }}"
    assert env["SESSION_ID"] == "${{ inputs.session }}"
    assert env["LIMIT"] == "${{ inputs.limit }}"


def test_setup_python_consumes_python_version_input() -> None:
    setup = [
        s
        for s in _steps()
        if str(s.get("uses", "")).startswith("actions/setup-python@")
    ]
    assert setup
    assert setup[0]["with"]["python-version"] == "${{ inputs.python-version }}"


def test_run_step_invokes_qa_pack_audit_with_declared_flags() -> None:
    run = _step_named("Run audit")["run"]
    assert "devin-qa-pack" in run
    for token in ("audit", "--session", "--all", "--limit", "--sessions-db"):
        assert token in run


@pytest.mark.skipif(BASH is None, reason="contract needs bash")
@pytest.mark.parametrize(
    ("env", "expected_argv"),
    [
        # defaults: sweep all sessions, capped at the declared limit
        (
            {"SESSION_ID": "", "LIMIT": "20", "SESSIONS_DB": ""},
            ["audit", "--all", "--limit", "20"],
        ),
        # a session id switches to single-session mode — --all/--limit
        # must not leak in (the CLI scopes --limit to --all)
        (
            {"SESSION_ID": "abc123", "LIMIT": "20", "SESSIONS_DB": ""},
            ["audit", "--session", "abc123"],
        ),
        # sessions-db forwards under the flag the CLI declares, and a
        # path with spaces must survive as a single argv element
        (
            {
                "SESSION_ID": "",
                "LIMIT": "5",
                "SESSIONS_DB": "/tmp/dir with space/sessions.db",
            },
            [
                "audit",
                "--all",
                "--limit",
                "5",
                "--sessions-db",
                "/tmp/dir with space/sessions.db",
            ],
        ),
        (
            {"SESSION_ID": "s1", "LIMIT": "20", "SESSIONS_DB": "/x/s.db"},
            ["audit", "--session", "s1", "--sessions-db", "/x/s.db"],
        ),
    ],
)
def test_audit_step_forwards_inputs_to_cli(
    env: dict, expected_argv: list, tmp_path: Path
) -> None:
    """Execute the action's run block against a stub ``devin-qa-pack``
    on PATH and compare the argv it receives — pins the env→flag
    wiring, not just substrings."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_out = tmp_path / "argv.txt"
    stub = bin_dir / "devin-qa-pack"
    stub.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$ARGV_OUT"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    # GitHub runs `shell: bash` steps as `bash -eo pipefail`, so the
    # snippet is exercised under the same flags the action gets.
    script = _step_named("Run audit")["run"]
    proc = subprocess.run(
        [BASH, "-eo", "pipefail", "-c", script],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "ARGV_OUT": str(argv_out), **env},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert argv_out.read_text(encoding="utf-8").splitlines() == expected_argv


def test_audit_fails_closed_when_nothing_is_audited(capsys, monkeypatch) -> None:
    """Zero audited sessions is exit 2, never a silent green — ``all()``
    on an empty list would otherwise let a CI gate pass on no
    evidence."""
    from devin_qa_pack import cli

    monkeypatch.setattr(cli, "collect_evidences", lambda *a, **k: ([], []))
    assert cli._cmd_audit(_audit_args("--all", "--limit", "20")) == 2
    assert "no sessions" in capsys.readouterr().err


def test_audit_unknown_session_fails_closed(capsys, monkeypatch) -> None:
    from devin_qa_pack import cli

    monkeypatch.setattr(cli, "collect_evidences", lambda *a, **k: ([], []))
    assert cli._cmd_audit(_audit_args("--session", "nope")) == 2
    assert "unknown session" in capsys.readouterr().err


def test_audit_exit_code_reflects_verdict(monkeypatch) -> None:
    """The action's step fails (non-zero) whenever any audited session
    is not PASS — that is what makes the composite a gate."""
    from devin_qa_pack import cli
    from devin_qa_pack.report import PASS

    evidence = SimpleNamespace(started_at="", native_id="s1", working_dir=None)
    monkeypatch.setattr(cli, "collect_evidences", lambda *a, **k: ([evidence], []))
    monkeypatch.setattr(cli, "render_text", lambda *a, **k: None)

    monkeypatch.setattr(
        cli, "audit_evidence", lambda *a, **k: SimpleNamespace(verdict=PASS)
    )
    assert cli._cmd_audit(_audit_args("--all")) == 0

    monkeypatch.setattr(
        cli, "audit_evidence", lambda *a, **k: SimpleNamespace(verdict="PARTIAL")
    )
    assert cli._cmd_audit(_audit_args("--all")) == 1
