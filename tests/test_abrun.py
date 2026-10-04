"""EV-5: A/B run — fail-closed, labelled, dry-run safe."""
import json
from devin_evals.cli import main


def test_abrun_dry_run_needs_no_bridge(tmp_path, capsys):
    assert main(["ab-run", "--task", "t", "--repo", str(tmp_path),
                 "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["would_create"] == ["ab-run:ab:a", "ab-run:ab:b"]


def test_abrun_fail_closed_without_bridge(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("DEVIN_BRIDGE_CMD", raising=False)
    rc = main(["ab-run", "--task", "t", "--repo", str(tmp_path)])
    assert rc == 2
    assert "DEVIN_BRIDGE_CMD" in capsys.readouterr().err
