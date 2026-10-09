"""Tests for the hermetic task pack under ``tasks/``.

Every task workspace is copied to ``tmp_path`` (originals are never
mutated in place); its deterministic check must fail in the shipped
state, then flipping to pass once the bundled ``_solution/`` overlay is
applied.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "tasks"
MANIFEST_PATH = TASKS_DIR / "manifest.json"

# Grading runs under the venv python when available (per pack docs),
# falling back to the interpreter running this suite.
VENV_PYTHON = Path.home() / ".venvs" / "devin-evals" / "bin" / "python"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

ENTRIES: list[dict] = (
    json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if MANIFEST_PATH.exists()
    else []
)
ENTRY_IDS = [e["id"] for e in ENTRIES]

EXPECTED_TYPES = {"trigger": 5, "control": 3}
EXPECTED_KINDS = {"bugfix": 3, "feature": 3, "refactor": 2}
# The prompt must never leak the experiment to the candidate.
BANNED_PROMPT_WORDS = ("skill", "rule", "g3")
# Hermeticity: task sources must stay stdlib-only and offline.
BANNED_IMPORTS = ("socket", "urllib", "requests", "http", "subprocess")


def _copy_task(entry: dict, dest_parent: Path) -> Path:
    """Copy a task workspace minus the solution overlay."""
    src = REPO_ROOT / entry["dir"]
    dest = dest_parent / entry["id"]
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("_solution", "__pycache__"),
    )
    return dest


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=180, check=False)


def _pytest(task_copy: Path) -> subprocess.CompletedProcess:
    return _run([PYTHON, "-m", "pytest", "tests", "-q"], task_copy)


def _structure_check(task_copy: Path) -> subprocess.CompletedProcess:
    return _run([PYTHON, "check_structure.py"], task_copy)


def _apply_solution(entry: dict, task_copy: Path) -> None:
    """Overlay the reference fix onto a copied workspace."""
    solution = REPO_ROOT / entry["dir"] / "_solution"
    shutil.copytree(solution, task_copy, dirs_exist_ok=True)


def test_manifest_shape() -> None:
    assert MANIFEST_PATH.exists(), "tasks/manifest.json missing"
    entries = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert len(entries) == 8
    types: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for e in entries:
        for key in ("id", "kind", "type", "dir", "prompt", "grading"):
            assert key in e, f"{e.get('id')}: missing key {key!r}"
        assert e["kind"] in EXPECTED_KINDS, e["id"]
        assert e["type"] in EXPECTED_TYPES, e["id"]
        types[e["type"]] = types.get(e["type"], 0) + 1
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
        assert e["dir"] == f"tasks/{e['id']}"
        lowered = e["prompt"].lower()
        for banned in BANNED_PROMPT_WORDS:
            assert banned not in lowered, (
                f"{e['id']}: prompt mentions {banned!r}")
    assert types == EXPECTED_TYPES
    assert kinds == EXPECTED_KINDS


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRY_IDS)
def test_task_layout(entry: dict) -> None:
    task_dir = REPO_ROOT / entry["dir"]
    assert task_dir.is_dir(), f"{entry['dir']} missing"
    assert (task_dir / "tests").is_dir()
    assert (task_dir / "conftest.py").is_file()
    assert (task_dir / "_solution").is_dir()
    # The prompt lives in the manifest only, never in the workspace.
    assert not (task_dir / "TASK.md").exists()
    if entry["kind"] == "refactor":
        assert (task_dir / "check_structure.py").is_file()
    for py_file in task_dir.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for banned in BANNED_IMPORTS:
            assert f"import {banned}" not in text, (
                f"{py_file}: imports {banned!r} — not hermetic")


@pytest.mark.parametrize("entry", ENTRIES, ids=ENTRY_IDS)
def test_task_grades_fail_then_pass(entry: dict, tmp_path: Path) -> None:
    task_copy = _copy_task(entry, tmp_path)

    pre = _pytest(task_copy)
    if entry["kind"] == "refactor":
        # Refactor tasks ship working code: tests pass, layout check fails.
        assert pre.returncode == 0, (
            f"{entry['id']}: refactor tests must pass pre-change\n"
            + pre.stdout + pre.stderr)
        pre_check = _structure_check(task_copy)
        assert pre_check.returncode != 0, (
            f"{entry['id']}: check_structure must fail pre-refactor")
    else:
        assert pre.returncode != 0, (
            f"{entry['id']}: tests must fail in the shipped state\n"
            + pre.stdout + pre.stderr)

    _apply_solution(entry, task_copy)

    post = _pytest(task_copy)
    assert post.returncode == 0, (
        f"{entry['id']}: tests must pass after applying _solution\n"
        + post.stdout + post.stderr)
    if entry["kind"] == "refactor":
        post_check = _structure_check(task_copy)
        assert post_check.returncode == 0, (
            f"{entry['id']}: check_structure must pass post-refactor\n"
            + post_check.stdout + post_check.stderr)
