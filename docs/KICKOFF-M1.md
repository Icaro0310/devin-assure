# KICKOFF M1 — devin-evals

Dedicated session for THIS repo. Template scaffold — fill with real
content. `docs/SPEC.md` EN, bilingual READMEs, logic in
`src/devin_evals/` + thin `cli.py`, small commits + Devin trailer, push,
STATUS.md + CHANGELOG.md.

## One sentence

An evaluation harness for agent work: define graded cases (input context +
rubric), replay them against recorded or live sessions, and score quality
over time — so "is the agent getting better?" has a number.

## Devin-native differentiator

Graders can inspect `tool_call_state` ground truth (which tools ran, with
what args) via devin-internals — rubrics like "must call devin-redact
before publishing" become checkable facts, not LLM vibes.

Dep: `"devin-internals-spec @ git+https://github.com/Icaro0310/devin-internals-spec.git@v0.2.0"`

## Scope (M1)

`src/devin_evals/`:
- `cases.py` — eval case format: `evals/<name>.yaml` (id, description,
  session_ref or prompt_context, rubric: list of checks).
- `graders.py` — deterministic graders (stdlib only): `contains`,
  `not_contains`, `tool_called(name,args_substr)`, `file_exists`,
  `exit_code`, `no_secrets` (reuse devin-redact patterns inline — vendor
  the regexes, do NOT depend on devin-redact).
- `runner.py` — run cases against a sessions.db (offline replay: score
  recorded transcripts) → `report.json` + markdown summary with per-case
  PASS/FAIL and aggregate score.

## CLI

- `devin-evals run --evals <dir> --sessions-db <db> [--out report]`
- `devin-evals list --evals <dir>`
- Read-only; deterministic (no network, no LLM calls in M1).

## Fixtures/tests

`devin_internals` fixtures + 3 sample evals (one passing, one failing,
one tool-call grader). Tests: each grader type, YAML parsing, runner
report shape, deterministic rerun.

## Env notes

`python`=3.11.9; `python -m pip` only; no multi-line `python -c`; Windows.
No PyYAML? — use `json` eval files instead if yaml isn't stdlib; decide
and document (recommend: `evals/<name>.json` to stay stdlib-only).

## Done

Tests green · CLI verified on fixture · docs real · pushed. M2 queue in
STATUS.md: LLM-as-judge opt-in, regression CI mode, baseline diff
("score went down"), PyPI.
