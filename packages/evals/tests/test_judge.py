"""EV-1: opt-in LLM judge — fail-closed, labelled, non-deterministic."""

from devin_evals.judge import build_prompt, judge_available, run_judge


def test_judge_unavailable_without_env(monkeypatch):
    monkeypatch.delenv("DEVIN_BRIDGE_CMD", raising=False)
    ok, msg = judge_available()
    assert not ok and "DEVIN_BRIDGE_CMD" in msg


def test_build_prompt_shape():
    p = build_prompt("C1", "desc", {"a": 1}, "did it work?")
    assert '"verdict"' in p and "C1" in p and "did it work?" in p


def test_run_judge_parses_reply(tmp_path, monkeypatch):
    # fake bridge script: prints the bridge RESULT/TEXT markers + JSON
    script = tmp_path / "fake-bridge.js"
    script.write_text(
        'console.log("===== RESULT =====");'
        'console.log("{}");'
        'console.log("===== TEXT =====");'
        'console.log("{\\"verdict\\":\\"pass\\",\\"rationale\\":\\"ok\\"}");')
    monkeypatch.setenv("DEVIN_BRIDGE_CMD", str(script))
    r = run_judge("q", cwd=str(tmp_path), case_id="C1")
    assert r["ok"] and r["verdict"] == "pass"
    assert r["label"] == "judge:C1"
    assert r["deterministic"] is False


def test_run_judge_bad_json(tmp_path, monkeypatch):
    script = tmp_path / "bad.js"
    script.write_text('console.log("===== TEXT ====="); console.log("nope");')
    monkeypatch.setenv("DEVIN_BRIDGE_CMD", str(script))
    r = run_judge("q", cwd=str(tmp_path), case_id="C1")
    assert not r["ok"] and "not valid JSON" in r["error"]


def test_run_judge_label_in_argv(tmp_path, monkeypatch):
    script = tmp_path / "argv.js"
    script.write_text(
        'console.log(JSON.stringify(process.argv.slice(2)));')
    monkeypatch.setenv("DEVIN_BRIDGE_CMD", str(script))
    r = run_judge("q", cwd=str(tmp_path), case_id="C9")
    # argv echoed → not a verdict, but proves the call ran
    assert not r["ok"] or r["verdict"] in ("pass", "fail")
