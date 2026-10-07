# Changelog

## Unreleased

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
