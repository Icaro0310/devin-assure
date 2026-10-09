"""EV-1: opt-in LLM judge via devin-bridge (isolated, non-deterministic).

The deterministic rubric graders are the default; a judge is opt-in and
explicitly labelled non-deterministic. It drives ``devin-bridge`` (which
itself gates through the active policy) to open a *labelled* session —
``judge:<case-id>`` — so ``devin-janitor`` can reap the noise later.

Environment contract (fail-closed):
- ``DEVIN_BRIDGE_CMD`` — path to ``bin/devin-bridge.js``; without it the
  judge refuses (no silent fallback to paid APIs).
- ``DEVIN_JUDGE_MODEL`` — optional model override; the bridge/ACP free
  model is the default (see the ecosystem's free-model rule).
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

DEFAULT_TIMEOUT_S = 300


def judge_available() -> tuple[bool, str]:
    cmd = os.environ.get("DEVIN_BRIDGE_CMD")
    if not cmd:
        return False, "DEVIN_BRIDGE_CMD not set — judge is opt-in and needs "
        "a devin-bridge binary"
    return True, cmd


def build_prompt(case_id: str, description: str, evidence: dict[str, Any],
                 question: str) -> str:
    return (
        "You are a deterministic-style grader. Read the evidence JSON and "
        "answer ONLY a JSON object {\"verdict\": \"pass\"|\"fail\", "
        "\"rationale\": \"<one sentence>\"}.\n\n"
        f"Case: {case_id} — {description}\n"
        f"Question: {question}\n\n"
        f"Evidence:\n```json\n{json.dumps(evidence, ensure_ascii=False)[:12000]}\n```"
    )


def run_judge(prompt: str, *, cwd: str, case_id: str,
              timeout_s: int = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """One judged call. Returns {ok, verdict?, rationale?, error?, model?}."""
    ok, cmd_or_reason = judge_available()
    if not ok:
        return {"ok": False, "error": cmd_or_reason}
    argv = ["node", cmd_or_reason, "prompt", cwd, prompt,
            "--label", f"judge:{case_id}", "--yes"]
    model = os.environ.get("DEVIN_JUDGE_MODEL")
    if model:
        argv += ["--model", model]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"judge timed out after {timeout_s}s"}
    except OSError as e:
        return {"ok": False, "error": f"cannot run bridge: {e}"}
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip()[-300:]
        return {"ok": False, "error": f"bridge exited {proc.returncode}: {tail}"}
    text = proc.stdout.split("===== TEXT =====")[-1].strip() or proc.stdout
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        verdict = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {"ok": False, "error": "judge reply was not valid JSON",
                "raw_tail": text[-200:]}
    if verdict.get("verdict") not in ("pass", "fail"):
        return {"ok": False,
                "error": f"invalid verdict: {verdict.get('verdict')!r}"}
    return {"ok": True, "verdict": verdict["verdict"],
            "rationale": str(verdict.get("rationale", ""))[:500],
            "label": f"judge:{case_id}", "deterministic": False}
