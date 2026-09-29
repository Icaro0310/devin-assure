# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial scaffold from `devin-repo-template`.
- `collect` — read `sessions.db` rows + `message_nodes`/`tool_call_state`
  counts and `acp-messages/*.db` usage rows via `devin-internals-spec`
  parsers; acp↔session join by filename stem; graceful degradation when the
  acp dir is missing.
- `aggregate` — per-day, per-project (`working_directory`), per-model
  rollups; totals/averages for cost, tokens, messages, tool calls,
  duration; top-N longest and most expensive sessions.
- `render` — GitHub-flavored markdown tables + `--json` output.
- `devin-metrics` CLI: `summary`, `projects`, `daily [--days N]`;
  `--data-dir`/`--sessions-db`/`--acp-dir` path overrides.
- `docs/SPEC.md`, `docs/SCHEMA.md` (assumed acp payload shape, unverified),
  real READMEs (EN + PT-BR), `STATUS.md`.
- Fixtures-first test suite: 36 tests on the real v17 DDL with synthetic
  controlled rows.
