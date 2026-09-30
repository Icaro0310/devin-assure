# Changelog

## 0.2.0

- Absorbed the `devin-dashboard` project: its collect/render/cli now live in
  `devin_metrics.dashboard`. New `devin-metrics dashboard` subcommand renders
  the static HTML dashboard (or `--json` dumps stats). The `devin-dashboard`
  console script remains as a compatibility alias.
- Merged all dashboard tests (58 total). The `devin-dashboard` repo is
  archived/deleted — this package is the canonical home.

## 0.1.0

- Initial release: local-only metrics CLI over Devin session stores.
