# Changelog

## 0.2.1

- `__version__` now matches the published artifact: the 0.2.0 wheel on PyPI self-reports 0.1.0 (built before the version sync landed). This release exists so the index carries a wheel whose metadata and self-report agree.

## Unreleased

- `devin-metrics-mcp` exits with a one-line install hint when the `mcp`
  extra is missing instead of dumping a traceback; startup
  `ImportError`s from `.run()` keep their traceback.

- Read-only adapters: `devin_metrics.mcp_server` MCP server
  (`metrics_query`, `devin-metrics-mcp` entry point, `mcp` extra),
  Devin skill and `adapters/` plugin root; setup documented in the OS
  guides. `metrics_query`'s `daily` default now matches the CLI
  (`days=0` returns all activity days).


- `devin-metrics` is now published on PyPI: README install section recommends `uv tool install devin-metrics` (DIST-STATUS banner removed, `distribution_status` is `published`).

- `labeler.yml` is now a thin caller of the shared reusable workflow in `devin-powerups` (`@v1`); PR labeling behavior is unchanged.

- `llms.txt` no longer states a hard-coded ecosystem size; the registry owns the count.
- README install section replaced by a generated `DIST-STATUS` banner stating the tool is source-only (no PyPI release yet) and offering both `pipx` and `uv` source installs.

## 0.2.0

- Absorbed the `devin-dashboard` project: its collect/render/cli now live in
  `devin_metrics.dashboard`. New `devin-metrics dashboard` subcommand renders
  the static HTML dashboard (or `--json` dumps stats). The `devin-dashboard`
  console script remains as a compatibility alias.
- Merged all dashboard tests (58 total). The `devin-dashboard` repo is
  archived/deleted — this package is the canonical home.

## 0.1.0

- Initial release: local-only metrics CLI over Devin session stores.
