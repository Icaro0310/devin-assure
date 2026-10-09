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

## Read from `message_nodes.metadata` (sessions.db)

| field | table | used for |
|---|---|---|
| `metadata.num_tokens_preceding` | message_nodes | cumulative context-window tokens per node — the **only** persisted token signal; reported per session as `context_tokens` (the session's peak value) |

## ✅ Verified: acp payload shape (2026-10, real install)

The `messages.payload` JSON was inspected on a real v17 install
(52 acp-messages DBs, thousands of rows). Findings:

- **No cost, no token, no model fields.** Payload keys are `id`, `kind`,
  `content`, `sourceEventTimestampMs`, `sourceEventIsStart`, `warnings`.
  Kinds observed: `user_message`, `agent_message`, `agent_thought`,
  `tool_call`.
- **`tool_call_state` carries no cost/usage columns either** (4 000 rows
  scanned, zero matches for cost/usage/token/price/billing keys).
- Per-turn cost exists **only in the live ACP session meta**
  (`total_credit_cost` / `total_acu_cost`) and is never written to disk —
  historical cost is unrecoverable from the local stores.
- `meta` table keys are `info`, `message_count`, `schema_version`
  (observed: `6`, plus `1` on two legacy DBs), `truncated`.
- `sessions.cogs_json` is prompt/permissions config (`core/plan_mask`,
  `core/smart_permission`), **not** cost-of-goods data.

`collect.extract_usage` is kept as the single forward-compatible adapter:
it still parses the documented shape below so a future payload change
touches one function — but on current stores it produces no records and
`cost_usd` is always `-` (unknown ≠ zero).

```json
{
  "model": "swe-2-high",
  "cost_usd": 0.0042,
  "usage": { "input_tokens": 1234, "output_tokens": 56 }
}
```

## Known gaps

- **Cost is unrecoverable locally** — verified. For historical analysis,
  `context_tokens` (peak `num_tokens_preceding`) is the best available
  proxy for session size.
- Orphan acp DBs (filename matches no session) cannot be attributed to a
  project or day — surfaced as `orphan_dbs` in the summary.
