# Changelog

## [Unreleased]

## [0.2.0] - 2026-10-16

### Fixed

- CLI compatibility: a `devin-qa-pack --version` probe (once per
  session, bypassing `devinQaPack.extraArgs`) distinguishes a genuinely
  old CLI from a bad setting when argparse rejects arguments — a bad
  setting now names `devinQaPack.extraArgs` instead of demanding an
  upgrade.
- Prerelease versions no longer bypass the compat floor: `0.1.2-rc1`
  classifies as too-old (semver precedence puts it below 0.1.2).

### Changed

- The CLI floor is patch-level `>=0.1.2,<0.3` (`--within` first shipped
  in 0.1.2); the README's advertised range was updated to match.

## [0.1.2]

### Fixed

- Executable resolution hardened for Restricted Mode (shared
  safe-executable resolver).

## [0.1.1]

### Added

- `devin-qa-pack: Re-run last audit` command; the status bar click now
  re-runs the same scoped audit (all / workspace / session) instead of
  always auditing globally.
- CI coverage: `test-qa-pack-vscode.yml` runs `node --test` via the
  reusable `adapter-test.yml` on Windows and Ubuntu.

### Fixed

- Multi-root workspaces: every workspace folder gets its own `--within`
  audit and the verdicts merge (deduped by session id), instead of only
  auditing the first folder.
- A claim-picker selection resolving after a newer audit started no
  longer opens stale claim details (seq re-checked post-await).
- Failure path clears the status bar warning background.
- `devinQaPack.extraArgs` can no longer smuggle `--online`,
  `--allow-domain`, `--transcript` or `--source` into the audit — the
  flags (and their values) are filtered before spawning the CLI.

## [0.1.0]

### Added

- Status bar verdict (worst of PASS / PARTIAL / UNVERIFIED across audited
  sessions) with per-verdict counts in the tooltip, click to re-audit.
- `devin-qa-pack: Audit all sessions`, `Audit this workspace's sessions`
  (`--within`) and `Audit one session` (`--session`), all backed by
  `devin-qa-pack audit --json`; quick-pick of verdicts opens a markdown
  claim sheet.
- Settings: `devinQaPack.executable`, `limit`, `extraArgs`.
- Strictly read-only: `report`, `session-end`, `intent` and `--online`
  stay CLI-only.
- `auditSeq` guard drops stale results from overlapping audits.
- `devin-qa-pack-cli` pin (`>=0.1,<0.2`) plus payload-shape validation
  with loud drift errors.
