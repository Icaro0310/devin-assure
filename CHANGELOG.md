# Changelog

## Unreleased

- **Docs** — refreshed the generated `Part of the DEVIN ecosystem` block: journey recuration v2 (six paths, zero repeats, `Local-first ops` label, `devin-bridge` in DevOps).
- **Docs** — ecosystem journey recuration applied (six curated audiences); stale `Path:` line removed from the devin-qa-pack eco-block, which is not a registry entry.
- **CI** — Ruff lint job added (`astral-sh/ruff-action`, pinned); codebase
  now lints clean.
- **Publish** — consolidated `pypi-publish.yml` builds and uploads
  `devin-evals`, `devin-metrics` and `devin-qa-pack` via PyPI Trusted
  Publishing (OIDC), tag `*-v*` or manual dispatch.
- **Fix** — `devin-qa-pack` CLI JSON report path restored
  (`_db`/`db` regression caught by the test suite).

## 2026-10 (F4 consolidation)

- Packages consolidated into this repo:
  `devin-evals` 0.2.0, `devin-metrics` 0.2.0, `devin-qa-pack` 0.1.0.
- Prior per-repo history lives in each package's git history.
