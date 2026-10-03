# devin-metrics

> **Unofficial community project.** Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.

**[Português (BR)](README.pt-BR.md)** · English

Local-only metrics for your Devin usage: sessions per day/week, cost and
token aggregates per project and model, longest sessions, tool-call mix —
zero telemetry, JSON + markdown output.

## The problem

Devin sessions accumulate real cost — tokens, model time, tool calls — but
there is no way to answer "what did I spend this week?" or "which project
eats my budget?". The data already exists on disk in `sessions.db` and
`acp-messages/*.db`; nothing reads it. `devin-metrics` is the missing read
side.

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
  all — the format is unpublished. This tool reads them directly, so cost
  comes from protocol data (`acp-messages`), not scraped text.
- **No-Devin:** remove Devin and there is nothing to measure — no store, no
  metrics.
- **One sentence:** it reads Devin's own databases and tells you what your
  sessions cost — locally, with nothing sent anywhere.

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

A missing `acp-messages` dir degrades gracefully: everything except
cost/token columns still works, and `cost_usd` shows `-` (unknown ≠ zero).

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

## Limitations

- **Read-only, no network.** Stores are opened `mode=ro`; nothing is
  written or sent anywhere.
- **Cost shape is assumed.** The acp `messages.payload` JSON carries
  model/cost fields per our reading — documented and marked *unverified* in
  `docs/SCHEMA.md`; the assumption is isolated in `collect.extract_usage()`
  so a real-shape fix touches one function.
- **Schema-gated.** `sessions.db` versions outside v15–v17 are refused
  loudly (via `devin-internals-spec`'s detector) rather than misread.
- Verified against synthetic fixtures only — a real install may surface
  shape drift (tracked in STATUS.md → M2).

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

## License

MIT — see [LICENSE](LICENSE).
