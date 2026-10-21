"""Static assertions for .github/workflows/release.yml.

The tag-triggered PyPI release leg builds and tests each package before
publishing. The evals-v0.3.1 release run failed because pytest was given
an explicit package path: pytest then collected tasks/*/conftest.py
siblings and broke on bare-module shadowing. Running bare pytest inside
the package directory lets each package's own ``testpaths`` setting
control collection; these tests pin that invocation shape.
"""

from pathlib import Path

import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "release.yml"
)


def _load() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    wf["on"] = wf.pop(True)
    return wf


def _build_and_test_run() -> str:
    steps = _load()["jobs"]["release"]["steps"]
    step = next(s for s in steps if s.get("name") == "Build and test")
    return step["run"]


def test_pytest_runs_inside_package_dir() -> None:
    """pytest must run from inside $PKG_DIR so testpaths is honored."""
    run = _build_and_test_run()
    assert '(cd "$PKG_DIR"' in run
    assert "python -m pytest" in run


def test_pytest_not_invoked_with_package_path() -> None:
    """An explicit "$PKG_DIR" argument would recollect task conftests."""
    run = _build_and_test_run()
    for line in run.splitlines():
        line = line.strip()
        if "pytest" in line and not line.startswith("#"):
            pytest_cmd = line.split("pytest", 1)[1]
            assert "$PKG_DIR" not in pytest_cmd
