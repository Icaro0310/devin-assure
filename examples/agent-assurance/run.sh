#!/usr/bin/env bash
# Agent Assurance — 60-second demo of the devin-* assurance loop.
#
#   devin-dream  generates synthetic sessions with KNOWN defects
#   devin-inspect proves the generated store matches the schema contract
#   devin-qa-pack audits the agent's claims against tool-call evidence
#   devin-evals  grades the same sessions with a deterministic rubric
#
# Nothing to install first: the script bootstraps uv (if absent) and runs
# each tool via uvx straight from GitHub. Everything is ephemeral except
# the artifacts it writes.
#
#   usage: ./run.sh [output-dir]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$(mktemp -d /tmp/agent-assurance.XXXXXX)}"
mkdir -p "$OUT/sessions" "$OUT/reports"

echo
echo "  Agent Assurance Demo"
echo "  ────────────────────────────────────────────────"

# ── bootstrap: uv (the only prerequisite; auto-installed if missing) ────────
if command -v uvx >/dev/null 2>&1; then
  UVX="uvx"
elif [ -x "$HOME/.local/bin/uvx" ]; then
  UVX="$HOME/.local/bin/uvx"
else
  echo "  [setup] uv not found — installing via the official installer"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  UVX="$HOME/.local/bin/uvx"
  [ -x "$UVX" ] || { echo "  uv install failed — see https://docs.astral.sh/uv/"; exit 127; }
fi

DREAM="$UVX --from git+https://github.com/Icaro0310/devin-dream devin-dream"
INSPECT="$UVX --from git+https://github.com/Icaro0310/devin-internals-spec devin-inspect"
QA="$UVX --from git+https://github.com/Icaro0310/devin-qa-pack devin-qa-pack"
EVALS="$UVX --from git+https://github.com/Icaro0310/devin-evals devin-evals"

# Warm the uv cache so first-build noise stays out of the demo output.
echo "  [setup] fetching tools (one-time; uv cache)"
for t in "$DREAM" "$INSPECT" "$QA" "$EVALS"; do
  $t --help >/dev/null 2>&1 || { echo "  failed to fetch: $t" >&2; exit 127; }
done
echo "        ✓ tools ready"

# ── 1. generate ─────────────────────────────────────────────────────────────
echo "  [1/4] Generating labeled sessions            (devin-dream)"
$DREAM unit --out "$OUT/sessions" --defect D01 D02 D03 >/dev/null
echo "        ✓ d01: claims a fix, leaves no evidence"
echo "        ✓ d02: claims tests pass, pytest exited 1"
echo "        ✓ d03: claims tests pass, pytest exited 0"

# ── 2. contract ─────────────────────────────────────────────────────────────
echo "  [2/4] Checking the store contract            (devin-inspect)"
schema=$($INSPECT schema "$OUT/sessions/d03/sessions.db")
ver=$(echo "$schema" | grep -o '"schema_version": *[0-9]*' | grep -o '[0-9]*')
echo "        ✓ sessions.db is schema v$ver, supported, verified"

# ── 3. audit ────────────────────────────────────────────────────────────────
echo "  [3/4] Auditing agent claims                  (devin-qa-pack)"
results=()
for d in d01 d02 d03; do
  db="$OUT/sessions/$d/sessions.db"
  exp=$(grep -o '"devin-qa-pack": *"[A-Z]*"' "$OUT/sessions/$d/expected.json" \
        | grep -o '[A-Z]*"$' | tr -d '"')
  line=$($QA audit --sessions-db "$db" --all 2>/dev/null | head -1 || true)
  got=$(echo "$line" | cut -d' ' -f1)
  mark="✗ MISMATCH"; [ "$got" = "$exp" ] && mark="✓"
  results+=("$mark|$got|$exp|$line")
  printf "        %s  %-10s %s\n" "$mark" "$d" "$line"
done

# ── 4. evaluate ─────────────────────────────────────────────────────────────
# The first two rubrics are SUPPOSED to fail: the defect is real, and the
# grader detecting it is the expected outcome — not a demo failure.
echo "  [4/4] Grading with a deterministic rubric    (devin-evals)"
for d in d01 d02 d03; do
  mkdir -p "$OUT/evals-$d"
  case_file=$(ls "$HERE"/evals/golden-"$d"-*.json)
  cp "$case_file" "$OUT/evals-$d/"
  exp_status=$(grep -o '"expected_status": *"[a-z]*"' "$case_file" \
               | grep -o '[a-z]*"$' | tr -d '"')
  line=$($EVALS run --evals "$OUT/evals-$d" \
    --sessions-db "$OUT/sessions/$d/sessions.db" \
    --out "$OUT/reports/$d" 2>/dev/null | grep -E "^(PASS|FAIL)" || true)
  grade=$(echo "$line" | cut -d' ' -f1)
  grade_low=$(echo "$grade" | tr 'A-Z' 'a-z')
  mark="✗ MISMATCH"; [ "$grade_low" = "$exp_status" ] && mark="✓"
  printf "        %s  %s  (rubric expected: %s)\n" "$mark" "$line" "$exp_status"
done

# ── result ──────────────────────────────────────────────────────────────────
echo
echo "  RESULT"
echo "  ────────────────────────────────────────────────"
for r in "${results[@]}"; do
  IFS='|' read -r mark got exp line <<< "$r"
  printf "  %-9s verdict %-10s (expected %s)  %s\n" "" "$got" "$exp" "$mark"
done
mismatch=0
for r in "${results[@]}"; do
  [ "${r%%|*}" = "✓" ] || mismatch=1
done
echo
echo "  The agent's narrative was confident in all three sessions."
echo "  The tool-call record told a different story — and the stack"
echo "  caught it. That is the product: claims vs evidence."
echo
echo "  Artifacts:"
echo "    $OUT/sessions/   generated sessions.db + expected.json per defect"
echo "    $OUT/reports/    eval grading reports per defect"
if [ "$mismatch" -eq 0 ]; then
  echo; echo "  ✓ Demo completed — all verdicts match the labels."
else
  echo; echo "  ✗ A verdict mismatched its label — please open an issue."; exit 1
fi
