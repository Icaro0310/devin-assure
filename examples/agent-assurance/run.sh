#!/usr/bin/env bash
# agent-assurance — reproducible end-to-end demo of the devin-* stack.
#
#   devin-dream generates controlled sessions with known defects
#     -> devin-inspect proves the store parses (schema contract)
#     -> devin-qa-pack audits the agent's claims against tool-call evidence
#     -> devin-evals replays a deterministic rubric on the same session
#
# Usage: ./run.sh [output-dir]   (default: a temp dir)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$(mktemp -d /tmp/agent-assurance.XXXXXX)}"
mkdir -p "$OUT/sessions" "$OUT/reports"

missing=0
for tool in devin-dream devin-inspect devin-qa-pack devin-evals; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "missing: $tool   (see README.md for install)" >&2
    missing=1
  fi
done
[ "$missing" -eq 0 ] || exit 127

echo "==> 1/4 devin-dream: generate labeled sessions"
devin-dream unit --out "$OUT/sessions" --defect D01 D02 D03

echo
echo "==> 2/4 devin-inspect: schema contract on a generated store"
devin-inspect schema "$OUT/sessions/d03/sessions.db"
devin-inspect sessions "$OUT/sessions/d03/sessions.db"

echo
echo "==> 3/4 devin-qa-pack: audit claims vs tool-call evidence"
for d in d01 d02 d03; do
  db="$OUT/sessions/$d/sessions.db"
  exp=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['expected']['devin-qa-pack'])" "$OUT/sessions/$d/expected.json")
  line=$(devin-qa-pack audit --sessions-db "$db" --all 2>&1 | head -1 || true)
  got=$(echo "$line" | cut -d' ' -f1)
  mark="OK"; [ "$got" = "$exp" ] || mark="MISMATCH"
  printf "  %-10s %s\n      %s\n" "$d" "$mark (expected $exp)" "$line"
done

echo
echo "==> 4/4 devin-evals: deterministic rubric grading"
for d in d01 d02 d03; do
  mkdir -p "$OUT/evals-$d"
  cp "$HERE"/evals/golden-"$d"-*.json "$OUT/evals-$d/"
  devin-evals run --evals "$OUT/evals-$d" \
    --sessions-db "$OUT/sessions/$d/sessions.db" \
    --out "$OUT/reports/$d" 2>&1 | tail -2 || true
done

echo
echo "Done. Artifacts in $OUT"
echo "  sessions/  generated sessions.db + expected.json per defect"
echo "  reports/   devin-evals grading reports"
