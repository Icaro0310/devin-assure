"""Static assertions for .github/workflows/release-qa-pack.yml.

The release fan-out has no unit-testable code, so these tests pin the
gates that keep a mistagged or manually dispatched run from publishing:
tag-push-only release/fanout, validate before either publish leg, the
reusable-workflow wiring, and the nonblocking PyPI re-dispatch.
"""

from pathlib import Path

import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "release-qa-pack.yml"
)
TAG_PUSH_ONLY = "github.event_name == 'push' && github.ref_type == 'tag'"


def _load() -> dict:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # YAML 1.1 parses the literal key `on` as boolean True.
    wf["on"] = wf.pop(True)
    return wf


def test_trigger_is_qa_pack_tag_push():
    wf = _load()
    assert wf["on"]["push"]["tags"] == ["qa-pack-v*"]
    assert "workflow_dispatch" in wf["on"]


def test_release_and_fanout_tag_push_only():
    jobs = _load()["jobs"]
    assert jobs["release"]["needs"] == "validate"
    assert jobs["release"]["if"] == TAG_PUSH_ONLY
    assert jobs["fanout"]["if"] == TAG_PUSH_ONLY


def test_fanout_requires_validate_and_release():
    fanout = _load()["jobs"]["fanout"]
    assert sorted(fanout["needs"]) == ["release", "validate"]
    assert fanout["uses"] == "Icaro0310/devin-powerups/.github/workflows/release.yml@v1"
    # Explicit secret mapping only — `inherit` would leak unrelated secrets.
    assert set(fanout["secrets"]) == {"OVSX_PAT"}


def test_pypi_redispatch_is_nonblocking():
    """A failed `gh workflow run` must not starve the fanout leg."""
    steps = _load()["jobs"]["release"]["steps"]
    dispatch = [s for s in steps if "gh workflow run" in s.get("run", "")]
    assert len(dispatch) == 1
    assert dispatch[0].get("continue-on-error") is True
    assert 'pypi-publish.yml --ref "$RELEASE_TAG"' in dispatch[0]["run"]


def test_release_uploads_vsix_onto_existing_release():
    """No gh release create here — the wheel workflow mints the release."""
    steps = _load()["jobs"]["release"]["steps"]
    lines = [
        line.strip()
        for s in steps
        for line in s.get("run", "").splitlines()
        if not line.strip().startswith("#")
    ]
    runs = "\n".join(lines)
    assert "gh release upload" in runs
    assert "gh release create" not in runs
