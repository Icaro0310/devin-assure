# SCHEMA notes — what devin-metrics assumes

Reference for the store fields this project consumes. Structural truth lives
in `devin-internals-spec` (`docs/SCHEMA.md` there, verified against v17);
this file only documents **what we read** and the one **assumption** we make.

## Read from `cli/sessions.db` (schema v15–v17, gated by the detector)

| field | table | used for |
|---|---|---|
| `sessions.id` | sessions | join key to `acp-messages/<id>.db` filename stem |
| `sessions.working_directory` | sessions | project attribution |
| `sessions.model` | sessions | declared model (per-model rollup, "sessions" column) |
| `sessions.created_at` / `last_activity_at` | sessions | duration, day buckets (epoch **ms**, UTC) |
| `sessions.title`, `sessions.hidden` | sessions | display, hidden count |
| `message_nodes` row count | message_nodes | messages per session (rows counted, content never read) |
| `tool_call_state` row count | tool_call_state | tool calls per session |

## Read from `User/acp-messages/<session-uuid>.db`

| field | table | used for |
|---|---|---|
| `messages.position`, `messages.kind` | messages | ordering, record identity |
| `messages.payload` | messages | JSON — see assumption below |
| filename stem | — | `session_id` join key |

## ⚠ Assumed acp payload shape (UNVERIFIED)

`devin-internals-spec` marks `messages.payload` **(unstable)** — the column
exists (verified) but the inner format was never inspected because it is row
content. devin-metrics therefore *assumes* usage-bearing rows look like:

```json
{
  "model": "swe-2-high",
  "cost_usd": 0.0042,
  "usage": { "input_tokens": 1234, "output_tokens": 56 }
}
```

Extraction rules (`collect.extract_usage` — the **single adapter**):

- `payload` must parse as a JSON **object**, else the row is ignored.
- `model`: top-level string, optional.
- `cost_usd`: top-level number, optional.
- `usage.input_tokens` / `usage.output_tokens`: ints inside a `usage`
  object, optional.
- A row carrying **none** of these fields contributes no `UsageRecord`
  (user chatter, tool calls, thoughts are ignored).

If a real install shows a different shape, the fix is *one function*:
`extract_usage()` in `src/devin_metrics/collect.py`. Every fixture row in
`tests/conftest.py` uses exactly this shape so the assumption is exercised
end-to-end.

## Known gaps

- Payload shape above is **not verified** against a real store (M2 task #1).
- Orphan acp DBs (filename matches no session) contribute to
  `cost_usd_total` / per-model rows but cannot be attributed to a project
  or day — surfaced as `orphan_dbs` in the summary.
- `sessions.metadata`/`cogs_json` may carry richer usage data in real
  installs — marked *(unstable)* upstream; not consumed in M1.
