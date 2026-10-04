# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `devin-qa-pack report` CLI: audits sessions and writes one
  deterministic, self-contained static HTML report (inline CSS, zero
  JavaScript, no external assets) — verdict counts, per-session table
  and per-claim breakdown. `--out`, `--session`, `--limit`.
- `http` claim kind: "the API returned 200"-style assertions are
  extracted from agent messages and verified against HTTP status codes
  recorded in `tool_call_state` payloads/output — verified (matching
  status), disputed (different status), unverifiable (no status
  recorded). No network, verification is against recorded output only.

- `devin-qa-pack audit` CLI: per-session or `--all` audits of a
  `sessions.db`, `--json` output, default-store auto-detection, exit
  codes 0/1/2.
- `claims` module: extracts deliverable claims (tests, commit hashes,
  file paths, pushes) from agent `message_nodes`.
- `verify` module: cross-checks claims against `tool_call_state` ground
  truth and `git log`/disk when the working directory is on disk —
  verified / disputed / unverifiable per claim.
- `report` module: `PASS` / `PARTIAL` / `UNVERIFIED` verdict per session.
- `docs/SPEC.md` + `SPEC.pt-BR.md`, real bilingual READMEs, `STATUS.md`.
- 45 fixtures-first tests on synthetic `sessions.db` fixtures +
  a real tmp git repo.
- Dependency on `devin-internals-spec` v0.2.0 (read-only parsers +
  schema gate).
- Initial scaffold from `devin-repo-template`.
