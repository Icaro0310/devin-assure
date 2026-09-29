# SPEC 01 — `devin-metrics` (M1)

## 1. Problem

Devin users have no idea what their usage costs. Sessions, token spend and
model mix accumulate inside local stores (`sessions.db`,
`acp-messages/*.db`) but nothing reads them: there is no usage page, no
export, no "what did I spend this week" answer. The data exists — it is just
invisible.

Evidence: `sessions.db` holds one row per session with `working_directory`,
`model` and timestamps; `acp-messages/*.db` holds the per-session ACP message
log. Both are documented in `devin-internals-spec` (verified against schema
v17) — the missing piece is a reader that turns them into numbers.

## 2. Devin extra (and the 3 tests)

**Extra:** metrics are grounded in Devin's **real stores** — not scraped
terminal text, not self-reported logs — and per-`working_directory` project
attribution comes free because Devin records the cwd of every session.

- **Side-by-side competitor:** generic token trackers (ccusage-style for
  other agents) count whatever the agent logs; they cannot read Devin's
  stores at all — the format is private and versioned (17 migrations).
- **No-Devin:** remove Devin → no `sessions.db`, no `acp-messages/` → the
  tool has literally nothing to measure. The extra disappears.
- **One sentence:** *"It reads Devin's own databases and tells you what your
  sessions cost — locally, with nothing sent anywhere."*

## 3. Scope (M1)

- `collect.py` — session rows + `message_nodes`/`tool_call_state` counts via
  `devin_internals.parsers.SessionsStore`; per-session usage records from
  `acp-messages/*.db` via `AcpMessagesStore`.
- `aggregate.py` — rollups: per-day, per-project, per-model; totals and
  averages (cost, messages, tool calls, duration); top-N longest and most
  expensive sessions.
- `render.py` — GitHub-flavored markdown tables; `--json` emits the same
  data as raw JSON.
- `cli.py` — thin argparse wrapper (all logic in the library).

Depends on `devin-internals-spec` v0.2.0 for the parsers + fixtures.

## 4. Non-scope

- **Read-only, no network, ever.** The stores are opened `mode=ro`; nothing
  is written, nothing is sent.
- Does not analyze message *content* (that is `devin-history`).
- Does not repair or diagnose the stores (that is `devin-doctor`).
- No daemon/watch mode, no cloud sync, no dashboard server (M2 queue).

## 5. Interfaces

| Interface | Description |
|---|---|
| Library `devin_metrics` | `collect → aggregate → render` pipeline |
| CLI `devin-metrics` | `summary` · `projects` · `daily [--days N]` |
| Output | markdown tables (default) · `--json` raw data |

CLI path resolution:

1. `--sessions-db` / `--acp-dir` flags win outright.
2. Else `--data-dir DIR` → `DIR/cli/sessions.db`, `DIR/User/acp-messages`.
3. Else the platform default: `%APPDATA%/devin` (Windows),
   `~/Library/Application Support/devin` (macOS), `~/.config/devin`
   (Linux).

## 6. Output format / data contract

- `aggregate.*` functions return **plain JSON-able dicts**; `None` means
  *unknown*, never zero. Cost fields are `float | None` USD.
- Attribution: sessions group by `working_directory`; acp DBs join to
  sessions by **filename stem = session id**. Orphan acp DBs (no session
  row) still count in `cost_usd_total` but not in `cost_usd_by_sessions`
  or project rows; the snapshot reports `orphan_dbs` so the gap is visible.
- `daily --days N` anchors on the **latest activity day in the data**, not
  on wall-clock now — deterministic for fixtures and for inspecting old
  installs.

## 7. Fixtures and tests (TDD — fixtures first)

1. `devin_internals.fixtures.create_sessions_db` provides the real v17 DDL;
   `tests/conftest.py` wipes generated rows and inserts a **controlled**
   session plan (2 projects × 2 days-spread, mixed models, exact
   message/tool counts) so aggregation math is asserted exactly.
2. Crafted acp `messages` rows carry the *assumed* model/cost JSON shape —
   documented in `docs/SCHEMA.md` and isolated in
   `collect.extract_usage()` so a real-shape fix is one function.
3. Tests: aggregation math · project/model/day grouping · `--json` shape ·
   missing-acp-dir graceful degradation · unreadable acp db counted, not
   fatal · missing sessions.db fails loudly.

## 8. Risks and mitigation

| Risk | Mitigation |
|---|---|
| acp payload shape is guessed | Single adapter `extract_usage()`; SCHEMA.md marks it *unverified*; M2 fix = one function |
| sessions.db schema changes | `SessionsStore` already gates on `detect_schema_version` (v15–v17); newer versions fail loudly |
| Row content is sensitive | Only counts/aggregates are emitted; no message text is read or printed |
| Orphan acp dbs skew totals | Tracked as `orphan_dbs`; totals keep both figures |

## 9. Definition of done (M1)

- [x] `collect`/`aggregate`/`render` + thin CLI (`summary`, `projects`,
      `daily`)
- [x] Fixtures-first tests, all green on Windows (`python -m pytest`)
- [x] `--json` contract stable and tested
- [x] Missing acp dir degrades gracefully; missing sessions.db errors
- [x] SCHEMA notes document the assumed acp payload shape
- [ ] Verified against a **real** Devin install (fixture-only so far)
- [ ] `pipx install devin-metrics` (PyPI)

## 10. Tasks / M2 queue

1. Verify the assumed acp payload shape on a real install → adjust
   `extract_usage` if needed.
2. Sparkline CLI charts for `daily`.
3. `export` → dashboard-friendly JSON bundle.
4. Weekly digest (`--since 7d` rollup, markdown report).
5. PyPI publish.
