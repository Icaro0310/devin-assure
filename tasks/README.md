# Hermetic task pack (G3)

Fully synthetic, self-contained task workspaces used for A/B attempts.
Each `tasks/<id>/` directory is a tiny Python project that an agent edits;
success is decided by a deterministic check, no judge involved.

## Layout

```
tasks/
  manifest.json          # the pack index — see below
  <id>/
    <module>.py          # (or a package/) the code the agent works on
    tests/test_*.py      # pytest check
    conftest.py          # puts the task dir on sys.path for the tests
    check_structure.py   # refactor tasks only: AST-based layout assert
    CONVENTIONS.md       # trigger tasks only: discoverable house style
    _solution/           # reference fix (NEVER copied into an attempt)
      NOTE.md            # one-paragraph description of the intended change
      <files...>         # post-state files, mirroring the task layout
```

`manifest.json` is a list of entries:

```json
{"id", "kind": "bugfix|feature|refactor", "type": "trigger|control",
 "dir": "tasks/<id>", "prompt": "<task text>", "grading": "<check>"}
```

The prompt lives **only** in the manifest — task dirs contain no
`TASK.md`, keeping the task text out of the workspace the agent sees.
Prompts must never mention skills, rules, or the experiment itself: a
task that teaches the candidate contaminates the measurement.

## The trigger/control rule

- **trigger** tasks ship a `CONVENTIONS.md` (and/or docstring
  conventions) describing a house style — naming, error-handling, module
  layout, public-API exports — that the agent must discover and follow
  for the check to pass. A fully reasonable solution that ignores the
  convention fails: `iter_active_users` vs `active_users`, exporting via
  `__init__.py` vs only defining the function, `ApiError` vs `KeyError`.
- **control** tasks are fully-specified generic work (math util bug,
  string helper, mechanical dedup) with no conventions to discover — a
  project-conventions aid should not change the outcome.

Pair every measurement on a trigger task with the same arm on a control
to separate "follows conventions better" from "just codes better".

## Grading

- bugfix / feature: `python -m pytest tests -q` inside the task dir must
  exit 0. Tests fail in the shipped state.
- refactor: tests already pass in the shipped state (they guard
  behavior); `python check_structure.py` asserts the required layout via
  the `ast` module and must exit 0 after the refactor.

`_solution/` holds the reference change: overlaying it on a fresh copy
of the task dir must flip the check from fail to pass. It exists so
pilots can sanity-check gradability — exclude it when materializing an
attempt workspace.

## Hermeticity requirements

- **Offline**: stdlib only, no network, no credentials, no real project
  code.
- **Deterministic**: same input always grades the same; no LLM judge, no
  clocks, no randomness.
- **Small**: ≤ ~60 lines of code + ≤ ~40 lines of tests per task; an
  attempt should finish well under 10 minutes.
- **Pristine**: originals are never mutated — each attempt copies the
  task dir (minus `_solution/`) into a scratch workspace first.

## Adding a task

1. `mkdir tasks/<id>/{tests,_solution}` and add the module, tests,
   `conftest.py` (copy an existing one), and for refactors a
   `check_structure.py`.
2. Decide trigger vs control first: trigger needs a discoverable
   convention the check enforces; control must not have one.
3. Add the manifest entry; write the prompt as a user would — no mention
   of skills/rules/the eval.
4. Add the reference fix under `_solution/` plus a `NOTE.md`.
5. Verify with `pytest tests/test_taskpack.py -q` from the repo root —
   it checks fail-then-pass for every task.
