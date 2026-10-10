---
name: devin-metrics
description: "Local-only activity metrics from Devin's session stores — when the user asks about usage patterns over time (sessions, projects, daily activity). Read-only; cost fields are never billing data."
triggers: [model, user]
allowed-tools:
  - exec
  - read
---

# devin-metrics

When the user asks about usage patterns over time — how many sessions,
which projects, which days — query the local stores:

```bash
devin-metrics summary --json
devin-metrics projects --json
devin-metrics daily --days 30 --json
```

Or, when this plugin's MCP server is connected, call `metrics_query`
with `kind` = `summary` | `projects` | `daily` — same JSON payload.

## Reading the result

- `summary`: headline counts (`sessions`, `messages`, `tool_calls`,
  `duration_ms_total`), a per-model table (`models[]`) and top-5 longest
  and costliest sessions.
- `projects`: one row per `working_directory` with session, message,
  tool-call and duration totals.
- `daily`: one row per UTC activity day; `days` keeps the N most recent
  activity days, anchored on the newest day in the data rather than on
  today (0 = all).
- `null` cost/token fields mean *unknown* — the local ACP logs carried
  no usage rows — never zero. Local stores persist no authoritative
  cost data, so never present a cost figure as a bill.
- An `error` key in the payload means the store could not be read
  (`bad_store`, `io`, `usage`) — report that, do not guess.

## Rules

- Read-only by design: the tool opens `sessions.db` and the
  `acp-messages` stores for reading only; it writes nothing, makes no
  network calls.
- `sessions_db` and `acp_dir` default to the platform Devin data dirs;
  pass explicit paths only for non-standard installs.
- A missing `acp-messages` dir degrades gracefully — cost/token fields
  come back `null`. Say "unknown", never "zero".
