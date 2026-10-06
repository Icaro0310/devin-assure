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

# Pinned tools: release tags where a release exists, commit SHAs otherwise
# (qa-pack is pinned to a post-v0.1.0 SHA because v0.1.0 predates the
# terminal_exit fix this demo relies on — move to the next tag when cut).
DREAM="$UVX --from git+https://github.com/Icaro0310/devin-dream@4f0e6e0761d61fda33d89a4c98086bff53d9519c devin-dream"
INSPECT="$UVX --from git+https://github.com/Icaro0310/devin-internals-spec@v0.3.0 devin-inspect"
QA="$UVX --from git+https://github.com/Icaro0310/devin-qa-pack@9e4456c8366c6696524388544600945612f5bdc1 devin-qa-pack"
EVALS="$UVX --from git+https://github.com/Icaro0310/devin-evals@d2355703723fbfc30738705d7eddd7b0ce0b26f2 devin-evals"

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
qa_matched=0; qa_total=0; qa_rows=()
for d in d01 d02 d03; do
  db="$OUT/sessions/$d/sessions.db"
  exp=$(grep -o '"devin-qa-pack": *"[A-Z]*"' "$OUT/sessions/$d/expected.json" \
        | grep -o '[A-Z]*"$' | tr -d '"')
  line=$($QA audit --sessions-db "$db" --all 2>/dev/null | head -1 || true)
  got=$(echo "$line" | cut -d' ' -f1)
  qa_total=$((qa_total + 1))
  case "$got" in
    PASS|PARTIAL|UNVERIFIED) ;;
    *) got="ERROR"; line="audit produced no verdict (harness failure)" ;;
  esac
  mark="✓"; [ "$got" = "$exp" ] && qa_matched=$((qa_matched + 1)) || mark="✗"
  qa_rows+=("        $mark  $d  $line")
  printf "        %s  %-10s %s\n" "$mark" "$d" "$line"
done

# ── 4. evaluate ─────────────────────────────────────────────────────────────
# The first two rubrics are SUPPOSED to fail: the defect is real, and the
# grader detecting it is the expected outcome — not a demo failure.
echo "  [4/4] Grading with a deterministic rubric    (devin-evals)"
ev_matched=0; ev_total=0; ev_rows=()
for d in d01 d02 d03; do
  mkdir -p "$OUT/evals-$d"
  case_file=$(ls "$HERE"/evals/golden-"$d"-*.json)
  cp "$case_file" "$OUT/evals-$d/"
  exp_status=$(grep -o '"expected_status": *"[a-z]*"' "$case_file" \
               | grep -o '[a-z]*"$' | tr -d '"')
  line=$($EVALS run --evals "$OUT/evals-$d" \
    --sessions-db "$OUT/sessions/$d/sessions.db" \
    --out "$OUT/reports/$d" 2>/dev/null | grep -E "^(PASS|FAIL)" || true)
  grade=$(echo "$line" | cut -d' ' -f1 | tr 'A-Z' 'a-z')
  ev_total=$((ev_total + 1))
  case "$grade" in
    pass|fail) ;;
    *) grade="error"; line="eval produced no verdict (harness failure)" ;;
  esac
  mark="✓"; [ "$grade" = "$exp_status" ] && ev_matched=$((ev_matched + 1)) || mark="✗"
  ev_rows+=("        $mark  $line  (rubric expected: $exp_status)")
  printf "        %s  %s  (rubric expected: %s)\n" "$mark" "$line" "$exp_status"
done

# ── result ──────────────────────────────────────────────────────────────────
echo
echo "  RESULT"
echo "  ────────────────────────────────────────────────"
echo "  $qa_matched/$qa_total QA verdicts matched their labels"
echo "  $ev_matched/$ev_total evaluation outcomes matched their labels"
echo
echo "  The agent's narrative was confident in all three sessions."
echo "  The tool-call record told a different story — and the stack"
echo "  caught it. That is the product: claims vs evidence."
echo
echo "  Artifacts:"
echo "    $OUT/sessions/   generated sessions.db + expected.json per defect"
echo "    $OUT/reports/    eval grading reports per defect"
if [ "$qa_matched" -eq "$qa_total" ] && [ "$ev_matched" -eq "$ev_total" ]; then
  echo; echo "  ✓ Demo completed successfully."
else
  echo; echo "  ✗ Demo failed — a verdict or eval outcome did not match its label:"
  for r in "${qa_rows[@]}" "${ev_rows[@]}"; do
    case "$r" in *"✗"*) echo "$r" ;; esac
  done
  exit 1
fi
