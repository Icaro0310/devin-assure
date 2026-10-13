"""Skill/plugin surface contract: the shipped skill exists, has valid
frontmatter, and the plugin manifest is self-consistent."""

import json
from pathlib import Path

ADAPTERS = Path(__file__).parents[1] / "adapters"
SKILL = ADAPTERS / "skills" / "devin-metrics" / "SKILL.md"
MANIFEST = ADAPTERS / ".devin-plugin" / "plugin.json"


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), "SKILL.md missing frontmatter"
    block = text.split("---", 2)[1]
    out = {}
    for line in block.strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


def test_skill_exists_with_required_frontmatter():
    assert SKILL.is_file()
    fm = _frontmatter(SKILL)
    assert fm["name"] == "devin-metrics"
    assert fm["description"]


def test_skill_is_read_only_by_text():
    body = SKILL.read_text(encoding="utf-8").lower()
    assert "read-only" in body
    for banned in ("--apply", "--fix", "--repair", "--delete", "vacuum"):
        assert banned not in body


def test_plugin_manifest_self_consistent():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["name"] == "devin-metrics"
    assert (ADAPTERS / "skills" / "devin-metrics" / "SKILL.md").is_file()
    servers = manifest.get("mcpServers", {})
    assert "devin-metrics" in servers
    assert "devin-metrics-mcp" in json.dumps(servers["devin-metrics"])
