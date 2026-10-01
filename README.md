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

```bash
pipx install devin-metrics
```

Until it is on PyPI, install from the repo:

```bash
pipx install git+https://github.com/Icaro0310/devin-metrics.git
```

## Usage

```bash
devin-metrics summary                  # headline numbers + top-5 lists
devin-metrics projects                 # per-project cost/session table
devin-metrics daily --days 14          # activity over time
devin-metrics summary --json           # raw JSON for scripting
```

By default the stores are located at the platform data dir
(`%APPDATA%/devin` on Windows). Override with `--data-dir`, or point at the
stores directly:

```bash
devin-metrics summary --sessions-db path/to/sessions.db --acp-dir path/to/acp-messages
```

A missing `acp-messages` dir degrades gracefully: everything except
cost/token columns still works, and `cost_usd` shows `-` (unknown ≠ zero).

## Platform support

Tested on **Windows and Linux** (`windows-latest` + `ubuntu-latest` in CI).
Devin's local stores are auto-detected per platform — `%APPDATA%` on
Windows, `~/.config/devin/` (XDG) on Linux, `~/Library/Application Support/devin/`
on macOS. Pass an explicit path to override (see Usage).

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
