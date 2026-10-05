# KICKOFF M1 — devin-metrics

You are the dedicated session for THIS repository. Scaffold from the
ecosystem template — fill with real content. Rules: `docs/SPEC.md` EN
canonical, shared README plus Windows/Linux platform guides, logic in `src/devin_metrics/` + thin
`cli.py`, small commits + Devin trailer, `git push`, STATUS.md + CHANGELOG.md.

## One sentence

Local-only observability for your Devin usage: sessions per day/week,
activity and context-size peaks per project and model, longest sessions,
tool-call mix — zero telemetry, JSON + markdown output.

## Devin-native differentiator

Reads Devin's real stores so metrics are grounded in persisted session data,
not scraped text — and per-`working_directory` project attribution comes
free. `docs/SCHEMA.md` verifies that cost fields are not persisted locally.

Dependency:
```toml
"devin-internals-spec @ git+https://github.com/Icaro0310/devin-internals-spec.git@v0.2.0",
```

## Scope (M1)

`src/devin_metrics/`:
- `collect.py` — pull session rows + persisted context/model fields via
  devin-internals parsers.
- `aggregate.py` — rollups: per-day, per-project, per-model; totals and
  averages (messages, tool calls); top-N longest sessions.
- `render.py` — markdown tables + `--json` raw data.

## CLI

- `devin-metrics summary [--sessions-db <db>] [--acp-dir <dir>] [--json]` —
  headline numbers.
- `devin-metrics projects` — per-project session/activity table.
- `devin-metrics daily [--days N]` — activity over time.
- Read-only, no network, ever.

## Fixtures/tests

`devin_internals.fixtures` for both stores; craft acp `messages` rows with
model/cost JSON fields (document the JSON shape you use in SCHEMA notes —
if the real shape differs, isolate it in one adapter function so M2 fixes
are one-line). Tests: aggregation math, project grouping, JSON shape,
missing-acp-dir graceful degradation.

## Env notes

`python`=3.11.9; `python -m pip` only; no multi-line `python -c`; Windows.

## Done

Tests green · CLI on fixture · docs real · pushed. M2 queue in STATUS.md:
sparkline CLI charts, export to dashboard JSON, weekly digest, PyPI.
