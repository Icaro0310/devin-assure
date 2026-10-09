# STATUS — devin-metrics

Updated: 2026-09-29 · Milestone: **M1 (done)** · Version: 0.1.0

## Done in M1

- `src/devin_metrics/paths.py` — platform data-dir resolution (same
  convention as `devin-doctor`: `%APPDATA%/devin`, `~/Library/Application
  Support/devin`, `~/.config/devin`).
- `src/devin_metrics/collect.py` — `collect(sessions_db, acp_dir)` →
  `MetricsSnapshot`: session rows + `message_nodes`/`tool_call_state` counts
  via `SessionsStore`; `UsageRecord`s via `AcpMessagesStore`. acp↔session
  join by **filename stem = session id**; orphan dbs counted, not dropped.
  `extract_usage()` is the single adapter holding the assumed acp payload
  shape (`docs/SCHEMA.md`). Missing acp dir → graceful (`acp_available`
  false, costs `None`); unreadable db files are counted, never fatal.
- `src/devin_metrics/aggregate.py` — `summarize`, `by_project`, `by_model`,
  `by_day(days=)`, `top_sessions`. `None` = unknown (never zero);
  `cost_usd_total` counts all acp usage incl. orphans,
  `cost_usd_by_sessions` only matched. `--days` anchors on latest activity
  day in the data (not wall-clock).
- `src/devin_metrics/render.py` — GFM markdown tables + `dumps_json`.
- `src/devin_metrics/cli.py` — thin argparse: `summary` / `projects` /
  `daily`, `--data-dir`/`--sessions-db`/`--acp-dir`/`--json`. Missing
  `sessions.db` → exit 1; missing acp dir → stderr warning + exit 0.
- `tests/` — fixtures-first: `conftest.py` builds the real v17 DDL via
  `devin_internals.fixtures`, wipes generated rows, inserts a controlled
  4-session / 2-project / 3-day plan plus crafted acp usage payloads (incl.
  1 orphan db) → all aggregation math asserted exactly.
- `docs/SPEC.md` (EN canonical), `docs/SCHEMA.md` (assumed payload shape,
  marked *unverified*), real READMEs (EN + PT-BR).
- **36 tests, all green** (Windows, Python 3.11.9, pytest 9.1.1). CLI smoke
  verified on the synthetic tree (`summary`/`projects`/`daily`, md + json).

## Environment notes

- `python` = 3.11.9 w/ pytest; bare `pip` → Python 3.14. Always
  `python -m pip`. `devin_internals` 0.2.0 already in site-packages;
  editable install used `--no-deps` (dep is a git URL → offline-safe).
- exec runs under **cmd.exe** with Git's usr/bin on PATH: `ls`/`cat`/`find`
  work, but no `for` loops, no `;` separators, no heredocs — commit messages
  use repeated `-m` flags.
- `.devin/` added to `.gitignore` (ecosystem convention).

## Decisions / notes

- acp payload shape `{"model", "cost_usd", "usage":{input,output}}` is an
  explicit assumption (upstream marks `payload` *unstable*). Isolated in
  `extract_usage()`; fixtures exercise it end-to-end.
- Usage rows with zero recognized fields are dropped, so user/tool chatter
  never inflates usage stats.
- `hidden` sessions are counted (`hidden_sessions` stat) but not filtered
  out — documented in SPEC.

## Remaining for M2 (per spec §10)

1. Verify assumed acp payload shape on a **real** install → adjust
   `extract_usage` if needed.
2. Sparkline CLI charts for `daily`.
3. `export` → dashboard-friendly JSON bundle.
4. Weekly digest (`--since 7d` rollup).
5. PyPI publish (`pipx install devin-metrics`) — name + account needed.

## Blockers

None.
