<div align="center">

<img src="assets/banner.svg" alt="devin-metrics" width="100%"/>

</div>

# devin-metrics

> **Unofficial community project.** Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.

**[Português (BR)](README.pt-BR.md)** · English

Local-only metrics for your Devin usage: sessions per day/week, per-project
and per-model rollups, context-size peaks, longest sessions, tool-call mix —
zero telemetry, JSON + markdown output.

## The problem

Devin sessions accumulate real activity — context growth, model time, tool
calls — but there is no way to answer "which project ate my week?" or "how
big did my sessions get?". The data already exists on disk in `sessions.db`
and `acp-messages/*.db`; nothing reads it. `devin-metrics` is the missing
read side.

## Prior art

Agent-usage trackers exist for other tools — e.g. `ccusage` for Claude Code
reads `~/.claude` transcripts and reports cost/token rollups. This project
adapts the same idea; it does not reinvent it. What is different here is the
*source*: Devin's stores are private, schema-versioned (17 migrations), and
undocumented — so the reading layer is delegated to
[`devin-internals-spec`](https://github.com/Icaro0310/devin-internals-spec)
which owns parsing + schema detection.

## What makes it Devin-native

- **Side-by-side:** generic token trackers cannot open Devin's stores at
  all — the format is unpublished. This tool reads them directly, so
  metrics come from protocol data (`sessions.db`, `acp-messages`), not
  scraped text.
- **No-Devin:** remove Devin and there is nothing to measure — no store, no
  metrics.
- **One sentence:** it reads Devin's own databases and tells you what your
  sessions did — locally, with nothing sent anywhere.

Per-session `working_directory` gives project attribution for free.

## Install

Python ≥ 3.10 and `pipx` are required. **Windows (PowerShell):** install `pipx` with `py -m pip install --user pipx`, run `py -m pipx ensurepath`, then reopen the terminal. **Linux (Debian/Ubuntu):** run `sudo apt install pipx python3-venv` and `pipx ensurepath`; reopen the terminal. Other Linux distributions should install `pipx` using their package manager.

This package is not on PyPI yet; install the public GitHub version:

```bash
pipx install "devin-metrics @ git+https://github.com/Icaro0310/devin-metrics.git"
```

## Usage

```bash
devin-metrics summary                  # headline numbers + top-5 lists
devin-metrics projects                 # per-project cost/session table
devin-metrics daily --days 14          # activity over time
devin-metrics dashboard --out usage.html

devin-dashboard build --out usage.html # dashboard executable alias
devin-dashboard data --json             # same dashboard source data as JSON
devin-metrics summary --json           # raw JSON for scripting
```

`devin-dashboard` is an alias shipped by the same package. Its `build` command
writes a standalone HTML dashboard; `data` prints the normalized stats payload.

By default, `sessions.db` is read from the platform data root (`%APPDATA%/devin`
on Windows, `$XDG_DATA_HOME/devin` on Linux, normally `~/.local/share/devin`).
ACP logs are read from the separate UI config root (`%APPDATA%/Devin/User`
on Windows, `$XDG_CONFIG_HOME/Devin/User` on Linux). Override with
`--data-dir`, `--sessions-db` or `--acp-dir`.

```bash
devin-metrics summary --sessions-db path/to/sessions.db --acp-dir path/to/acp-messages
```

A missing `acp-messages` dir degrades gracefully: `cost_usd` shows `-`
(unknown ≠ zero). Note that per-turn cost is **not persisted** even when
the dir exists (verified — see Limitations); `context_tokens` is the real
per-session token signal.

## Works with Devin alone (Devin-only mode)

All metrics are computed locally from Devin's own stores and written to a
local database — zero telemetry, zero network calls. The `devin-dashboard`
console alias included in this package (it absorbed the old standalone
dashboard) also renders entirely on your machine.

## Platform support

Tested on **Windows and Linux** (`windows-latest` + `ubuntu-latest` in CI).
The CLI database is auto-detected from `%APPDATA%/devin/cli/sessions.db` on
Windows and `$XDG_DATA_HOME/devin/cli/sessions.db` on Linux (default
`~/.local/share/devin/cli/sessions.db`). ACP logs are read from
`$XDG_CONFIG_HOME/Devin/User/acp-messages` (default
`~/.config/Devin/User/acp-messages`). Legacy `~/.config/devin` layouts are
also checked. Override with `--sessions-db` or `--acp-dir`.


The dashboard also charts **peak `num_tokens_preceding` per day** — the only token signal persisted locally (verified: no cost fields are stored). Cost charts show a "no data" note rather than fake zeros.


### `devin-metrics churn` (needs `devin-graph build`)

Rework stats from the knowledge graph: files re-touched by multiple tool calls in the same session, per session and per model. Known noise: pseudo-paths like `/dev/null` and shell builtins can rank high — they are real `file_touched` edges, just not meaningful rework.

## Limitations

- **Read-only, no network.** Stores are opened `mode=ro`; nothing is
  written or sent anywhere.
- **Cost is not persisted locally — verified.** A real install (2026-10)
  confirms acp payloads and `tool_call_state` carry **no** cost/token
  fields; per-turn cost lives only in the live ACP session meta and is
  never written to disk. `cost_usd` therefore shows `-` on real data.
  The one token signal that *does* persist — `num_tokens_preceding` in
  `message_nodes.metadata` — is reported per session as `context_tokens`
  (peak context size). Details: `docs/SCHEMA.md`.
- **Schema-gated.** `sessions.db` versions outside v15–v17 are refused
  loudly (via `devin-internals-spec`'s detector) rather than misread.
- Drift-checked — `devin-inspect contract` (from devin-internals-spec)
  validates this install against every known contract boundary.

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

## When to use this

- You want to know what your Devin usage costs: totals per project, model,
  or day, plus longest sessions and tool-call mix.
- You need a scriptable JSON feed of usage stats (`--json` on every command).
- You want a standalone HTML dashboard of activity (`devin-metrics dashboard`
  or the `devin-dashboard` alias).
- Telemetry is a hard no — everything is computed and stored locally.

## When NOT to use this

- You need to search message content — use `devin-search`; or relationship
  queries across sessions/files/tools — use `devin-graph`.
- You need live, real-time session monitoring — use `devin-office`.
- The machine has no Devin CLI/Desktop install — there is nothing to measure.

## FAQ

**What is devin-metrics?** A local CLI that reads Devin's own session
databases and reports usage metrics: sessions per day/week, cost and token
totals per project and model, longest sessions, and tool-call mix. It also
ships a `devin-dashboard` alias that writes a standalone HTML dashboard.

**How does devin-metrics get cost data?** Honest answer: it mostly
doesn't — verified on a real install, Devin's local stores persist **no**
cost or token fields (cost exists only in the live ACP session meta and
is never written to disk). What it does measure: sessions, messages,
tool calls, durations, per-project/per-model rollups, and `context_tokens`
(peak `num_tokens_preceding` — the only token signal that persists). The
`extract_usage()` adapter remains ready if a future schema starts
persisting cost.

**Does devin-metrics send data anywhere?** No. All metrics are computed
locally and written to a local database. There are no network calls and no
telemetry; Devin's own stores are opened `mode=ro` and never written.

## License

MIT — see [LICENSE](LICENSE).
