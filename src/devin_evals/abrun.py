"""EV-5: A/B eval — run two variants in fresh bridge sessions, grade both.

This is the G3 gate: instead of replaying recorded sessions, it *creates*
two sessions through ``devin-bridge`` — variant A and variant B (e.g. with
vs without a skill injected into the prompt) — then grades each against
the same rubric and diffs the scores.

Honest cost note: this creates real sessions and consumes tokens; it is
opt-in by design and every session is labelled ``ab-run:<tag>:<variant>``
so ``devin-janitor`` can reap the noise. Fail-closed without
``DEVIN_BRIDGE_CMD`` (see judge.py).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from devin_evals.judge import judge_available

DEFAULT_TIMEOUT_S = 45 * 60


def _bridge_prompt(bridge: str, repo: str, prompt: str, label: str,
                   timeout_s: int) -> dict[str, Any]:
    """One bridge session + prompt; returns {ok, session_id?, error?}."""
    argv = ["node", bridge, "prompt", repo, prompt,
            "--label", label, "--yes",
            "--timeout-ms", str(timeout_s * 1000)]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_s + 30)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "bridge prompt timed out"}
    except OSError as e:
        return {"ok": False, "error": f"cannot run bridge: {e}"}
    if proc.returncode != 0:
        return {"ok": False,
                "error": (proc.stderr or proc.stdout).strip()[-300:]}
    # parse the RESULT json block
    out = proc.stdout
    session_id = None
    if "===== RESULT =====" in out:
        blob = out.split("===== RESULT =====")[1].split("=====")[0]
        try:
            session_id = json.loads(blob).get("sessionId")
        except (ValueError, json.JSONDecodeError):
            pass
    return {"ok": True, "session_id": session_id}


def run_ab(task_prompt: str, variant_a: str, variant_b: str, *,
           repo: str, tag: str = "ab",
           timeout_s: int = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Run both variants; returns session ids + per-variant results."""
    ok, bridge = judge_available()
    if not ok:
        return {"ok": False, "error": bridge}
    results = {}
    for variant, prefix in (("a", variant_a), ("b", variant_b)):
        prompt = f"{prefix}\n\n{task_prompt}" if prefix else task_prompt
        results[variant] = _bridge_prompt(
            bridge, repo, prompt, f"ab-run:{tag}:{variant}", timeout_s)
    return {"ok": all(r["ok"] for r in results.values()),
            "variants": results,
            "note": "sessions are labelled ab-run:<tag>:<variant> — "
                    "janitor can reap them; this consumed real tokens"}
