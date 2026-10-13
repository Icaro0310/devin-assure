---
name: devin-evals
description: "Replay deterministic eval cases against a recorded sessions.db — before declaring a regression task done, run the eval cases and read the score. Read-only; offline rubric grading, no sessions are created."
triggers: [model, user]
allowed-tools:
  - exec
  - read
---

# devin-evals

Before declaring a regression task done, replay the eval cases against
the sessions.db that recorded the work — never assert "fixed" on a run
you have not executed:

```bash
devin-evals run --evals <dir> --sessions-db <sessions.db> --out <dir>
```

Or, when this plugin's MCP server is connected, call `evals_run` with
the same arguments — it returns the same report dict as `report.json`
and writes nothing.

## Reading the result

- `summary.score` is `passed / total`; `skipped` (no `session_ref`) and
  `errored` (harness/config bug) count as not passed.
- Each entry in `cases[]` carries `status` (`pass` / `fail` / `skip` /
  `error`), `score`, `detail`, and per-check rows with `grader`,
  `passed`, `error`, `detail`.
- `session_ref` resolves exact ids, exact titles, `latest`,
  `project:<substr>` and `window:<start>:<end>` — the most recent match
  wins.
- An `error` key at the top level means the run could not start
  (`bad_evals`, `bad_store`, `io`) — report that, do not guess.

## Rules

- Read-only by design. Deterministic rubric grading over a recorded
  store — nothing is written to it, no sessions are created, no LLM is
  called.
- Only claim a regression is fixed when `summary.failed` and
  `summary.errored` are both zero. A `fail` is a finding to surface;
  an `error` case is a harness bug, not an agent failure.
