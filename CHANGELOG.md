# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `devin_evals.cases` — `evals/<name>.json` case format (JSON chosen over
  YAML to stay stdlib-only) with eager validation (`CaseError`).
- `devin_evals.graders` — six deterministic graders over an `Evidence`
  value object: `contains`, `not_contains`, `tool_called`, `file_exists`,
  `exit_code`, `no_secrets` (secret regexes vendored from devin-redact).
- `devin_evals.runner` — offline replay against `sessions.db` via
  `devin-internals-spec`; deterministic `report.json` + `report.md` with
  per-case PASS/FAIL/SKIP/ERROR and aggregate score.
- `devin-evals` CLI: `run` (exit 0/1/2) and `list` subcommands.
- `devin_evals.demo` — synthetic sessions.db builder
  (`python -m devin_evals.demo <db>`) for trying the CLI without Devin.
- `evals/` — three sample cases (pass, intentionally-fail, tool-call).
- `docs/SPEC.md`, `STATUS.md`, real bilingual READMEs; 62 tests.
- Initial scaffold from `devin-repo-template`.
