# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `devin-evals corpus generate|verify` (EV-3) — deterministic golden corpus
  of labeled synthetic defect sessions (D01–D09, devin-dream catalogue)
  plus matching eval cases with `expected_status`/`known_gap` metadata;
  `verify` is the CI gate comparing expected-vs-actual. Session specs come
  from `devin_dream.defects` when importable, else the vendored copy in
  `devin_evals._vendored_dream` (identical corpora either way).
- Committed golden corpus at `corpus/` (`evals/*.json` + `corpus.json`;
  the generated `sessions*.db` stay gitignored) with
  `tools/regen-corpus.py` rebuilding it deterministically — `--check`
  fails CI when the committed corpus diverges from regenerated output.
- `runner._message_text` now also reads the `content` key in
  `chat_message` blobs (the ACP/dream shape), not only `text`.
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
