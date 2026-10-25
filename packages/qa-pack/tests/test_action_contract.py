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
  every audited session passed;
- the ``comment`` leg is reporting, never a gate: ``--out`` is only
  requested when a comment can actually be posted (an artifact write
  failure exits 2 and must not flip a passing audit), the marker
  lookup paginates and only edits bot-owned comments (a forged marker
  means a fresh comment, not a hijack), the artifact path is unique
  per invocation, and the posted body carries aggregates only — no
  session titles, uncovered claim names or local ``sessions_db``
  paths.
"""

from __future__ import annotations

import json
import os
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
        env={
            "PATH": os.pathsep.join((str(bin_dir), "/usr/bin", "/bin")),
            "ARGV_OUT": str(argv_out),
            **env,
        },
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


# ---------------------------------------------------------------------
# comment leg — reporting, never a gate
# ---------------------------------------------------------------------

JQ = shutil.which("jq")
PYTHON3 = shutil.which("python3")


def _comment_step() -> dict:
    return _step_named("Comment verdict")


def _run_step(step: dict, env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run a step's ``run`` block the way GitHub does: bash -eo pipefail."""
    return subprocess.run(
        [BASH, "-eo", "pipefail", "-c", step["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_comment_inputs_declared() -> None:
    inputs = _load()["inputs"]
    comment = inputs["comment"]
    assert comment["default"] == "auto"
    assert comment["required"] is False
    # gh pr comment authenticates through the token input, defaulting
    # to the caller's GITHUB_TOKEN — never hard-code a credential.
    assert inputs["token"]["default"] == "${{ github.token }}"


def test_comment_step_is_fail_open() -> None:
    """The comment leg can never change the gate: it runs after the
    audit regardless of its outcome (``always()``), is declared
    continue-on-error, disables -e and returns 0 on every path."""
    step = _comment_step()
    assert "always()" in step["if"]
    assert step.get("continue-on-error") is True
    run = step["run"]
    assert "set +e" in run
    assert "::warning::" in run
    assert run.rstrip().endswith("exit 0")


def test_comment_step_condition_matches_input_contract() -> None:
    """`if:` gates on the same semantics the audit step uses before
    adding --out: never/false off, always/true or pull_request on."""
    cond = _comment_step()["if"]
    assert "inputs.comment != 'never'" in cond
    assert "inputs.comment != 'false'" in cond
    assert "inputs.comment == 'always'" in cond
    assert "inputs.comment == 'true'" in cond
    assert "github.event_name == 'pull_request'" in cond


def test_marker_lookup_paginates_and_checks_bot_ownership() -> None:
    """Re-runs must find the previous verdict comment on PRs with >100
    comments (--paginate + jq -s 'add // []'), take the LATEST match and
    only ever edit a bot-authored comment (.user.login allowlist) — a
    human-pasted marker is a forgery and must not be hijacked."""
    run = _comment_step()["run"]
    assert "--paginate" in run
    assert "jq -s" in run
    assert "add // []" in run
    assert ".user.login" in run
    assert "github-actions[bot]" in run
    assert "<!-- devin-qa-pack-audit -->" in run
    assert "| last |" in run


def test_audit_out_path_unique_per_invocation() -> None:
    """The sweep artifact is keyed to this invocation (run id, attempt
    and a per-step nonce) and cleared before the audit runs — a second
    use of the action cannot repost a stale verdict."""
    run = _step_named("Run audit")["run"]
    assert "GITHUB_RUN_ID" in run
    assert "GITHUB_RUN_ATTEMPT" in run
    assert "rm -f" in run
    assert "GITHUB_ENV" in run
    assert "QA_AUDIT_JSON" in run


def test_out_requested_only_when_comment_will_run() -> None:
    """Structural pin for the --out gate: the audit step mirrors the
    comment step's `if:` — never/false, empty, and non-PR events on
    `auto` must keep --out off the command line."""
    run = _step_named("Run audit")["run"]
    assert "GITHUB_EVENT_NAME" in run
    assert "pull_request" in run
    assert '"$COMMENT"' in run
    for literal in ('"never"', '"false"', '"always"', '"true"'):
        assert literal in run


def test_comment_body_is_aggregate_only() -> None:
    """The posted body carries verdict + counts per session id and the
    run URL — session titles, claim names and the sessions_db path
    belong to the artifact, not to a public PR comment."""
    run = _comment_step()["run"]
    for field in (
        "session_id",
        "verdict",
        "verified",
        "disputed",
        "unverifiable",
        "actions/runs/",
    ):
        assert field in run
    for leak in ("sessions_db", "uncovered", '"title"', "title()"):
        assert leak not in run


@pytest.mark.skipif(BASH is None, reason="contract needs bash")
@pytest.mark.parametrize(
    ("comment", "event", "expect_out"),
    [
        ("never", "pull_request", False),
        ("false", "pull_request", False),
        ("", "pull_request", False),
        ("auto", "push", False),
        ("auto", "pull_request", True),
        ("always", "push", True),
        ("true", "push", True),
        ("always", "pull_request", True),
    ],
)
def test_audit_step_adds_out_only_when_comment_posts(
    comment: str, event: str, expect_out: bool, tmp_path: Path
) -> None:
    """Execute the audit run block with a stub ``devin-qa-pack``:
    ``--out`` appears exactly when the comment leg's `if:` would be
    true — otherwise an artifact write failure could flip a passing
    audit to exit 2 for a comment that can never be posted."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_out = tmp_path / "argv.txt"
    stub = bin_dir / "devin-qa-pack"
    stub.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$ARGV_OUT"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    github_env = tmp_path / "github_env"

    proc = _run_step(
        _step_named("Run audit"),
        env={
            "PATH": os.pathsep.join((str(bin_dir), "/usr/bin", "/bin")),
            "ARGV_OUT": str(argv_out),
            "SESSION_ID": "",
            "LIMIT": "20",
            "SESSIONS_DB": "",
            "COMMENT": comment,
            "GITHUB_EVENT_NAME": event,
            "GITHUB_RUN_ID": "4242",
            "GITHUB_RUN_ATTEMPT": "3",
            "GITHUB_ENV": str(github_env),
            "RUNNER_TEMP": str(tmp_path),
        },
    )
    assert proc.returncode == 0, proc.stderr
    argv = argv_out.read_text(encoding="utf-8").splitlines()
    if not expect_out:
        assert "--out" not in argv
        assert not github_env.exists()
        return

    assert "--out" in argv
    out_path = Path(argv[argv.index("--out") + 1])
    assert out_path.parent == tmp_path
    assert re.fullmatch(r"devin-qa-pack-audit-4242-3-.+-.+\.json", out_path.name)
    # the path reached the comment step via GITHUB_ENV
    assert (
        f"QA_AUDIT_JSON={out_path}"
        in github_env.read_text(encoding="utf-8").splitlines()
    )


@pytest.mark.skipif(BASH is None, reason="contract needs bash")
def test_audit_step_artifact_path_differs_across_invocations(
    tmp_path: Path,
) -> None:
    """Two uses of the action in one job must not share the artifact —
    run the block twice, the --out paths must differ."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_out = tmp_path / "argv.txt"
    stub = bin_dir / "devin-qa-pack"
    stub.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$ARGV_OUT"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = {
        "PATH": os.pathsep.join((str(bin_dir), "/usr/bin", "/bin")),
        "ARGV_OUT": str(argv_out),
        "SESSION_ID": "",
        "LIMIT": "20",
        "SESSIONS_DB": "",
        "COMMENT": "auto",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_RUN_ID": "4242",
        "GITHUB_RUN_ATTEMPT": "3",
        "GITHUB_ENV": str(tmp_path / "github_env"),
        "RUNNER_TEMP": str(tmp_path),
    }
    paths = []
    for _ in range(2):
        proc = _run_step(_step_named("Run audit"), env=env)
        assert proc.returncode == 0, proc.stderr
        argv = argv_out.read_text(encoding="utf-8").splitlines()
        paths.append(argv[argv.index("--out") + 1])
    assert paths[0] != paths[1]


# ---------------------------------------------------------------------
# comment leg — stubbed-gh behavioral cases
# ---------------------------------------------------------------------

GH_STUB = """#!/usr/bin/env bash
# Records every call in $GH_STUB_LOG, one "CALL <argv>" line each.
# Serves the canned issues-comments listing from $GH_STUB_RESPONSE
# (and an open-PR number from $GH_STUB_PRLIST for `gh pr list`).
printf 'CALL %s\\n' "$*" >> "${GH_STUB_LOG:-/dev/null}"
if [ "${1:-}" = "pr" ] && [ "${2:-}" = "list" ]; then
  cat "${GH_STUB_PRLIST:-/dev/null}"
  exit 0
fi
for arg in "$@"; do
  case "$arg" in
    *"/comments?per_page=100"*)
      cat "${GH_STUB_RESPONSE:-/dev/null}"
      exit 0
      ;;
  esac
done
exit 0
"""

AUDIT_PAYLOAD = {
    "version": "9.9.9",
    "sessions_db": "/home/alice/private/sessions.db",
    "sessions": [
        {
            "session_id": "sess-aaa",
            "title": "refactor the payroll db credentials",
            "verdict": "PASS",
            "summary": {"verified": 3, "disputed": 0, "unverifiable": 1},
            "uncovered": ["coverage"],
        },
        {
            "session_id": "sess-bbb",
            "title": "move /home/alice/.ssh to vault",
            "verdict": "PARTIAL",
            "summary": {"verified": 1, "disputed": 2, "unverifiable": 0},
            "uncovered": ["push"],
        },
    ],
}

MARKER = "<!-- devin-qa-pack-audit -->"


def _gh_comment(cid: int, login: str) -> dict:
    return {
        "id": cid,
        "body": f"old verdict {MARKER}",
        "user": {"login": login},
    }


def _comment_env(tmp_path: Path, *, pr_number: str = "5") -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "gh"
    stub.write_text(GH_STUB, encoding="utf-8")
    stub.chmod(0o755)
    payload = tmp_path / "audit.json"
    payload.write_text(json.dumps(AUDIT_PAYLOAD), encoding="utf-8")
    (tmp_path / "calls.log").touch()
    return {
        "PATH": os.pathsep.join(
            (
                str(bin_dir),
                os.environ.get("PATH", "/usr/bin:/bin"),
            )
        ),
        "GH_TOKEN": "test-token",
        "PR_NUMBER": pr_number,
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REF_NAME": "feat/x",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "777",
        "GITHUB_RUN_ATTEMPT": "1",
        "RUNNER_TEMP": str(tmp_path),
        "QA_AUDIT_JSON": str(payload),
        "GH_STUB_LOG": str(tmp_path / "calls.log"),
        "GH_STUB_RESPONSE": str(tmp_path / "comments.json"),
        "GH_STUB_PRLIST": str(tmp_path / "prlist.txt"),
    }


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    if not log.exists():
        return []
    return log.read_text(encoding="utf-8").splitlines()


def _body(tmp_path: Path) -> str:
    body = tmp_path / "audit.json.comment.md"
    return body.read_text(encoding="utf-8") if body.exists() else ""


@pytest.mark.skipif(
    BASH is None or JQ is None or PYTHON3 is None,
    reason="contract needs bash+jq+python3",
)
def test_comment_step_posts_fresh_when_no_marker(tmp_path: Path) -> None:
    (tmp_path / "comments.json").write_text("[]", encoding="utf-8")
    proc = _run_step(_comment_step(), env=_comment_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    calls = _calls(tmp_path)
    assert any("pr comment 5" in c for c in calls)
    assert not any("PATCH" in c for c in calls)


@pytest.mark.skipif(
    BASH is None or JQ is None or PYTHON3 is None,
    reason="contract needs bash+jq+python3",
)
def test_comment_step_updates_latest_bot_comment(tmp_path: Path) -> None:
    """Two bot comments — the LATEST one is the one we PATCH."""
    (tmp_path / "comments.json").write_text(
        json.dumps(
            [
                _gh_comment(111, "github-actions[bot]"),
                _gh_comment(222, "github-actions[bot]"),
            ]
        ),
        encoding="utf-8",
    )
    proc = _run_step(_comment_step(), env=_comment_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    calls = _calls(tmp_path)
    assert any("PATCH" in c and "issues/comments/222" in c for c in calls)
    assert not any("pr comment" in c for c in calls)


@pytest.mark.skipif(
    BASH is None or JQ is None or PYTHON3 is None,
    reason="contract needs bash+jq+python3",
)
def test_comment_step_never_edits_forged_marker(tmp_path: Path) -> None:
    """A human-authored comment containing the marker must NOT be
    patched — the action posts a fresh bot comment instead of
    overwriting somebody else's."""
    (tmp_path / "comments.json").write_text(
        json.dumps([_gh_comment(222, "pr-author")]),
        encoding="utf-8",
    )
    proc = _run_step(_comment_step(), env=_comment_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    calls = _calls(tmp_path)
    assert any("pr comment 5" in c for c in calls)
    assert not any("PATCH" in c for c in calls)
    assert not any("issues/comments/222" in c for c in calls)


@pytest.mark.skipif(
    BASH is None or JQ is None or PYTHON3 is None,
    reason="contract needs bash+jq+python3",
)
def test_comment_step_prefers_own_comment_over_forged(tmp_path: Path) -> None:
    """Forged marker sitting newer than ours: the filter must drop it
    so `last` still lands on the bot's own older comment."""
    (tmp_path / "comments.json").write_text(
        json.dumps(
            [
                _gh_comment(111, "github-actions[bot]"),
                _gh_comment(999, "pr-author"),
            ]
        ),
        encoding="utf-8",
    )
    proc = _run_step(_comment_step(), env=_comment_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    calls = _calls(tmp_path)
    assert any("PATCH" in c and "issues/comments/111" in c for c in calls)
    assert not any("issues/comments/999" in c for c in calls)


@pytest.mark.skipif(
    BASH is None or JQ is None or PYTHON3 is None,
    reason="contract needs bash+jq+python3",
)
def test_comment_step_skips_missing_or_empty_artifact(
    tmp_path: Path,
) -> None:
    """Stale-artifact regression: if the audit failed before writing,
    the comment step warns and exits 0 without touching gh."""
    env = _comment_env(tmp_path)
    for missing in (
        str(tmp_path / "never-written.json"),
        str(tmp_path / "empty.json"),
    ):
        Path(env["GH_STUB_LOG"]).unlink(missing_ok=True)
        if missing.endswith("empty.json"):
            Path(missing).touch()
        env["QA_AUDIT_JSON"] = missing
        proc = _run_step(_comment_step(), env=env)
        assert proc.returncode == 0, proc.stderr
        assert "skipping PR comment" in proc.stdout
        assert _calls(tmp_path) == []


@pytest.mark.skipif(
    BASH is None or JQ is None or PYTHON3 is None,
    reason="contract needs bash+jq+python3",
)
def test_comment_step_resolves_open_pr_on_non_pr_event(
    tmp_path: Path,
) -> None:
    """comment: always on a push — the step resolves the open PR for
    the pushed branch through `gh pr list` before posting."""
    (tmp_path / "comments.json").write_text("[]", encoding="utf-8")
    (tmp_path / "prlist.txt").write_text("42\n", encoding="utf-8")
    env = _comment_env(tmp_path, pr_number="")
    env["GITHUB_EVENT_NAME"] = "push"
    proc = _run_step(_comment_step(), env=env)
    assert proc.returncode == 0, proc.stderr
    calls = _calls(tmp_path)
    assert any("pr list" in c for c in calls)
    assert any("pr comment 42" in c for c in calls)


@pytest.mark.skipif(
    BASH is None or JQ is None or PYTHON3 is None,
    reason="contract needs bash+jq+python3",
)
def test_comment_body_has_no_private_detail(tmp_path: Path) -> None:
    """The rendered body carries verdicts and counts keyed by session
    id — and never session titles, uncovered claim names, local
    sessions_db paths or home-directory fragments."""
    (tmp_path / "comments.json").write_text("[]", encoding="utf-8")
    proc = _run_step(_comment_step(), env=_comment_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    body = _body(tmp_path)
    assert "sess-aaa" in body and "sess-bbb" in body
    assert "PARTIAL" in body and "PASS" in body
    assert MARKER in body
    assert "actions/runs/777" in body
    for leak in (
        "payroll",
        "credentials",
        ".ssh",
        "/home/alice",
        "sessions.db",
        "uncovered",
    ):
        assert leak not in body
