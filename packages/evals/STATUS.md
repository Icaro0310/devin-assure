# STATUS — devin-evals

Updated: 2026-09-29 · Milestone: **M1 (done)** · Version: 0.1.0

## Done in M1

- `src/devin_evals/cases.py` — `evals/<name>.json` case format (id,
  description, `session_ref` or `prompt_context`, rubric). Eager
  validation: malformed JSON, missing/empty rubric, unknown grader, missing
  required params all raise `CaseError` naming the file. `id` defaults to
  the file stem; cases sorted by filename.
- `src/devin_evals/graders.py` — `Evidence`/`ToolCall`/`CheckResult` value
  objects + six deterministic graders: `contains`, `not_contains`,
  `tool_called(name, args_substr, min_calls)`, `file_exists`,
  `exit_code(value, mode=all|any|last)`, `no_secrets`. Secret regexes
  vendored from devin-redact (secret categories only).
- `src/devin_evals/runner.py` — offline replay against `sessions.db` via
  `devin_internals.SessionsStore` (schema-gated). `session_ref` resolves by
  id then title. Statuses pass/fail/skip/error; deterministic
  `report.json` + `report.md` (reruns byte-identical).
- `src/devin_evals/cli.py` — `devin-evals run` (exit 0 pass / 1 failures /
  2 usage-or-IO) and `devin-evals list`. Read-only.
- `src/devin_evals/demo.py` — `python -m devin_evals.demo <db>` builds a
  synthetic sessions.db with a scripted demo session (named tool calls,
  exit codes, working dir = repo root).
- `evals/` — 3 samples: `demo-pass` (contains/not_contains/exit_code/
  no_secrets), `demo-fail` (intentional FAIL), `demo-toolcall`
  (tool_called ×2 + file_exists).
- `docs/SPEC.md` (EN), real READMEs (EN/PT-BR).
- **62 tests, all green** (Windows, Python 3.11.9, pytest 9.1.1).

Verified: `devin-evals run --evals evals --sessions-db demo.db --out report`
prints `FAIL/PASS/PASS`, score 2/3, exit 1; `report.md` renders the per-case
table + check details.

## Post-M1

- **EV-4** — reusable rubric packs (`bugfix`/`feature`/`refactor`), a
  `"packs"` key in cases (pack checks run before the case's own), and
  `devin-evals packs` / `--packs-dir`.
- **EV-3** — golden corpus: `devin-evals corpus generate|verify` builds a
  seeded corpus from the devin-dream defect catalogue (vendored fallback),
  with per-case `expected_status`/`known_gap`; committed at `corpus/`
  (`evals/*.json` + `corpus.json`; `sessions*.db` regenerated, gitignored)
  and drift-gated by `tools/regen-corpus.py --check` in CI.
- **EV-5/G3 v2** — `ab-run` is now the preregistered A/B suite harness:
  tasks manifest (index `manifest.json` like the shipped `tasks/` pack,
  or per-task `*.json`; ≥5 trigger + ≥3 control), k attempts per arm
  (min 3), seeded ABBA/BAAB interleave, budget caps that abort and
  report partials, per-attempt `shutil.copytree` isolation (`_solution/`
  never copied), labels `g3-ab:<task>:<variant>:<attempt>`, and a
  `g3-report/0.1` report whose `design` block freezes tasks/k/seed/caps/
  thresholds *before* the first session. Verdicts come from
  `g3stats.py` (Wilson CIs, fixed-seed 10k bootstrap of per-task
  deltas): regresses / improves / no-detectable-effect / inconclusive.
  `--aa` measures the harness false-positive rate; `--calibration`
  gates `improves`. Session-running is injectable (`runner`/`grader`
  callables) so every ordering/stat/isolation path is tested
  bridge-free. `--task` keeps the deprecated two-session quick mode.

## Environment notes

- `python` = 3.11.9 w/ pytest 9.1.1; always `python -m pip`.
- exec runs under **cmd.exe**, not bash: no heredocs, no multi-line
  `python -c`, `$VAR` is literal; commit messages need repeated `-m` flags.
- `devin-internals-spec` 0.2.0 installed in site-packages; pyproject pins
  the git tag `v0.2.0`.
- `pytest pythonpath=["src"]` so tests run from a checkout without install.

## Decisions / notes

- **JSON eval files** (not YAML): stdlib-only runtime; documented in
  SPEC §9.
- `contains`/`not_contains` grade the *transcript* only — tool-call JSON is
  excluded so an arg can't satisfy "the agent said X". `no_secrets` scans
  transcript **and** tool JSON (secrets leak via args).
- `file_exists` resolves relative paths under the session's recorded
  `working_directory`.
- `score = passed/total`; skip/error count as not-passed.
- Fixture DBs generated at test time (never committed binaries); real
  Devin DBs are never written to — the store opens `mode=ro`.

## Remaining for M2 (per spec §11)

1. LLM-as-judge graders (opt-in, flagged non-deterministic in the report).
2. Baseline diff (`--baseline report-old.json` → "score went down").
3. Regression CI mode (exit code from the diff, not just pass/fail).
4. Live mode for `prompt_context` cases (spawn a session, then grade).
5. PyPI publish (`pipx install devin-evals`).

## Blockers

None.
