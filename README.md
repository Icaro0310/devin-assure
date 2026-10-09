# devin-assure

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15338/badge)](https://www.bestpractices.dev/projects/15338)

<!-- DEVIN-ECO:BEGIN -->
> **Part of the [DEVIN ecosystem](https://github.com/Icaro0310/awesome-devin)**  
> Track: Verify · Nature: product  
> For: QA engineers, Developers  
> Interface: CLI  
> Path: QA engineers · step 1/3 — before `devin-evals`
<!-- DEVIN-ECO:END -->

Verification for Devin sessions: audit deliverable claims against
tool-call ground truth, replay recorded sessions against rubric graders,
and observe local activity without telemetry.

| Package | PyPI | What it does |
|---|---|---|
| [`packages/qa-pack`](packages/qa-pack) | `devin-qa-pack` | QA audit of session claims (tests, commits, files, pushes) vs `tool_call_state` |
| [`packages/evals`](packages/evals) | `devin-evals` | Deterministic eval harness: replay sessions against rubric graders (includes `dream` synthetic-session generator) |
| [`packages/metrics`](packages/metrics) | `devin-metrics` | Local-only session observability: activity, context size, token peaks |

> **Renamed (Oct 2026):** this repository moved from `Icaro0310/devin-qa-pack` to `Icaro0310/devin-assure` when it became the `devin-assure` product workspace. PyPI packages and console scripts keep their names; stars, issues and history are preserved by the redirect.

## Layout

```
packages/<name>/   one installable package each (src layout, own tests)
```

Each package ships independently: a tag `qa-pack-vX.Y.Z` (same for
`evals`, `metrics`) publishes only that package. CI is scoped per path —
a change under `packages/evals/` runs only the evals suite.

The standalone `devin-evals` and `devin-metrics` repositories were
absorbed into this workspace (F4.2); their histories are preserved under
`packages/` and the old repos are archived with pointers here.

## Platform support

All packages support Linux, macOS and Windows. Per-package guides live
under `packages/<name>/`.

> **Unofficial community project.** Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.
