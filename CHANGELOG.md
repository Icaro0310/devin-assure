# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-05

### Added

- `devin-qa-pack intent <session>` CLI (QA-4): prompt intent vs.
  touched-path coverage. Reads the session's first user message,
  extracts the paths/modules/repo names it references, extracts every
  path seen in `tool_call_state` payloads and reports
  `possibly_missed` (prompt-named paths never touched) and
  `scope_drift` (touched paths with no prompt-named anchor).
  Deliberately conservative heuristics — only file-grade references
  can be "missed"; slash-words, bare dir mentions and URL tokens never
  are. Statuses `aligned`/`flagged`/`skipped`; exit `0` aligned, `1`
  flagged or not computable, `2` could not run. The `session-end` side
  file gains an `intent` field with the same analysis.
- `devin-qa-pack session-end` CLI (QA-1): live audit of only the
  session that just ended — built to run as a `SessionEnd` hook
  handler. Session id resolves from `--session-id`, the hook payload
  on stdin (`{"session_id": ...}`), `$DEVIN_SESSION_ID`, or falls back
  to the most recently active session. Writes the verdict to a JSON
  side file (`<data-dir>/qa/<session-id>.json`, `--out`/`--data-dir`
  override) containing `{session_id, verdict, claims, audited_at}` and
  prints a one-line summary. `--limit` bounds claims verified.
  Fail-soft: always exits 0 once it ran (unresolvable sessions yield a
  `SKIPPED` verdict); non-zero only on usage errors.
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
