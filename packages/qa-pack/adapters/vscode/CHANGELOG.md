# Changelog

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
