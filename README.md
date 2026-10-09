# devin-assure

<div align="center">

<a href="https://github.com/Icaro0310/devin-assure/actions/workflows/ci.yml"><img src="https://github.com/Icaro0310/devin-assure/actions/workflows/ci.yml/badge.svg" alt="ci"/></a>
<a href="https://www.bestpractices.dev/projects/15338"><img src="https://www.bestpractices.dev/projects/15338/badge" alt="OpenSSF Best Practices"/></a>
<a href="https://scorecard.dev/viewer/?uri=github.com/Icaro0310/devin-assure"><img src="https://api.scorecard.dev/projects/github.com/Icaro0310/devin-assure/badge" alt="OpenSSF Scorecard"/></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT"/></a>
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+"/></a>
<a href="https://github.com/Icaro0310/devin-assure"><img src="https://img.shields.io/github/stars/Icaro0310/devin-assure" alt="GitHub stars"/></a>
<a href="https://github.com/Icaro0310/devin-assure/commits/main"><img src="https://img.shields.io/github/last-commit/Icaro0310/devin-assure" alt="Last commit"/></a>
<a href="https://github.com/Icaro0310/awesome-devin"><img src="https://img.shields.io/badge/part%20of-devin--*-ecosystem-7c3aed" alt="devin-* ecosystem"/></a>
<a href="https://github.com/Icaro0310/devin-assure/issues"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen" alt="PRs welcome"/></a>
</div>

<!-- DEVIN-ECO:BEGIN -->
> **Part of the [DEVIN ecosystem](https://github.com/Icaro0310/awesome-devin)**  
> Track: Verify · Nature: product  
> For: QA engineers, Developers  
> Interface: CLI  
> Path: QA engineers · step 1/3 — before `devin-evals`
<!-- DEVIN-ECO:END -->

<!-- DEVIN-WHERE:BEGIN -->
## Where this fits

- **Job:** Verify
- **Product:** [`devin-assure`](https://github.com/Icaro0310/devin-assure)
- **Packages:** `qa-pack` · `evals` · `metrics`
- **Mode:** read-only
- **Foundation:** [`devin-internals-spec`](https://github.com/Icaro0310/devin-internals-spec)
- **Ecosystem:** [`awesome-devin`](https://github.com/Icaro0310/awesome-devin) · registry: [`devin-powerups`](https://github.com/Icaro0310/devin-powerups)
<!-- DEVIN-WHERE:END -->

Verification for Devin sessions: audit deliverable claims against
tool-call ground truth, replay recorded sessions against rubric graders,
and observe local activity without telemetry.

| Package | PyPI | What it does |
|---|---|---|
| [`packages/qa-pack`](packages/qa-pack) | [![devin-qa-pack](https://img.shields.io/pypi/v/devin-qa-pack)](https://pypi.org/project/devin-qa-pack/) | QA audit of session claims (tests, commits, files, pushes) vs `tool_call_state` |
| [`packages/evals`](packages/evals) | [![devin-evals](https://img.shields.io/pypi/v/devin-evals)](https://pypi.org/project/devin-evals/) | Deterministic eval harness: replay sessions against rubric graders (includes `dream` synthetic-session generator) |
| [`packages/metrics`](packages/metrics) | [![devin-metrics](https://img.shields.io/pypi/v/devin-metrics)](https://pypi.org/project/devin-metrics/) | Local-only session observability: activity, context size, token peaks |

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
