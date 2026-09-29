# devin-evals

> **Unofficial community project.** Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.

**[Português (BR)](README.pt-BR.md)** · English

An evaluation harness for agent work: define graded cases (session +
rubric), replay them against recorded Devin sessions, and score quality
over time — so "is the agent getting better?" has a number.

## The problem

You tune prompts, rules files and models — and judge the result by vibes,
one anecdotal run at a time. There is no regression signal. Meanwhile every
Devin session already records, in `sessions.db`, its full transcript and
the `tool_call_state` table: *which tools ran, with what arguments, with
what exit codes*. That ground truth is sitting unused on your disk.

## Prior art

Generic eval frameworks ([OpenAI evals](https://github.com/openai/evals),
[promptfoo](https://github.com/promptfoo/promptfoo),
[Braintrust](https://www.braintrust.dev/)) grade *output text* — usually
through an LLM judge. SWE-bench-style harnesses grade public repos, not
your agent's real sessions. devin-evals adapts the rubric/graders idea; it
does not reinvent it. What it adds is the corpus: deterministic checks
over Devin's recorded tool-call ground truth.

## What makes it Devin-native

Rubrics like **"must call `devin_redact` before publishing"** become
checkable facts. Graders read `tool_call_state` via
[`devin-internals-spec`](https://github.com/Icaro0310/devin-internals-spec),
so a check asserts "tool `run_shell` was invoked with `pytest` in its args
and exited 0" — a fact, not an LLM judgement call.

- *Side-by-side:* promptfoo cannot assert "tool X was called with args
  containing Y" — it never sees Devin's tool-call table.
- *No-Devin:* no `sessions.db`, no tool-call graders, no replay mode.

## Install

```bash
pipx install devin-evals   # once on PyPI
# from a checkout:
pip install -e .
```

Requires Python ≥3.10. Runtime deps: `devin-internals-spec` only — no
network, no LLM calls.

## Usage

Write cases in `evals/*.json`:

```json
{
  "id": "redact-before-publish",
  "description": "Report workflow must stay hygienic",
  "session_ref": "Refinery session 2026-09-29",
  "rubric": [
    { "grader": "tool_called", "name": "devin_redact" },
    { "grader": "tool_called", "name": "run_shell", "args_substr": "pytest" },
    { "grader": "exit_code", "value": 0, "mode": "all" },
    { "grader": "contains", "text": "all tests pass" },
    { "grader": "no_secrets" }
  ]
}
```

`session_ref` matches a session **id or title** in `sessions.db`. Then:

```bash
devin-evals list --evals evals
devin-evals run --evals evals --sessions-db "$APPDATA/Devin/cli/sessions.db" --out report
```

`run` writes `report/report.json` + `report/report.md` (per-case
PASS/FAIL/SKIP/ERROR + aggregate score; reruns are byte-identical) and
exits 0 if everything passed, 1 on failures, 2 on usage/IO errors.

**Try it without a real Devin install:**

```bash
python -m devin_evals.demo demo.db
devin-evals run --evals evals --sessions-db demo.db
```

The shipped `evals/` directory contains a passing case, an intentionally
failing case, and a tool-call-ground-truth case.

### Graders

| grader | what it checks |
|---|---|
| `contains` / `not_contains` | literal substring in the transcript (case-sensitive) |
| `tool_called` | tool `name` called ≥`min_calls`, optional `args_substr` on call JSON |
| `file_exists` | path on disk — relative resolves under the session's `working_directory` |
| `exit_code` | recorded exit codes match `value` per `mode` (`all`/`any`/`last`) |
| `no_secrets` | zero secret-shaped strings (vendored devin-redact patterns) in transcript + tool JSON |

## Limitations

- **Offline replay only** (M1): grades recorded sessions, cannot spawn new
  ones. `prompt_context`-only cases report SKIP.
- **Deterministic only** (M1): no LLM-as-judge; `contains` is a literal,
  case-sensitive substring — it cannot tell "all tests pass" from "not all
  tests pass" semantically.
- Depends on Devin's *private, versioned* internals — a `sessions.db`
  schema bump makes `devin-internals` refuse loudly rather than misread.
- `tool_called` infers the tool name from `name`/`tool_name`/`tool`/`kind`
  keys in `tool_call_json`; unknown shapes degrade to "tool never called"
  details, not crashes.
- `file_exists` checks the filesystem *now* — replaying an old session
  whose workspace was cleaned will fail that check.

## Development

```bash
pip install -e ".[dev]"
python -m pytest     # 62 tests
```

## License

MIT — see [LICENSE](LICENSE).
