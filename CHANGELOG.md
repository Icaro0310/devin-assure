# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `labeler.yml` is now a thin caller of the shared reusable workflow in `devin-powerups` (`@v1`); PR labeling behavior is unchanged.

- Install section now recommends pypi `uv tool install devin-qa-pack`; `install.sh` defaults to the PyPI package (`DEVIN_QA_PACK_REF` opts back into a git ref) as the primary route, with `pipx`/source installs documented as alternatives.
- `examples/agent-assurance` — session generation now runs
  `devin-evals dream unit` (`devin-dream` was absorbed into
  `devin-evals`); the evals git pin moved to the 0.2.0 commit, which
  also lets the devkit updater recognize the upgrade.

### Added

- `audit --source mcp --session <id>` — audits **cloud sessions** through
  the official Devin MCP `devin_session_events` stream
  (`mcp.devin.ai`, `Bearer $DEVIN_API_KEY`, optional `DEVIN_ORG_ID` for
  explicit org context). `shell_process_started`/`terminal_update`/
  `shell_process_completed` events map onto `ParsedToolCall` with real
  commands, base64 stdout and first-class exit codes; `file`/`git`/`mcp`/
  `browser` categories map generically (kind from category, status from
  the event-type suffix). Verified end-to-end against a live cloud
  session (`PASS` on a file claim, evidence traced to the event stream).
- Experimental transcript adapters (`audit --transcript`): `aider`
  (`.aider.chat.history.md`) and `claude-code` (session `.jsonl`) feed
  the same claim/evidence model through `adapters.SourceSession`;
  `adapters/devin.py` wraps the native `sessions.db` source. `--format`
  overrides extension detection; `--cwd` sets the git/file check root.
- `install.sh` — one-liner installer (pipx preferred, `pip --user`
  fallback, `DEVIN_QA_PACK_REF` to pin a tag).
- The release workflow attaches a CycloneDX SBOM (`sbom.cdx.json`) to
  GitHub Releases.

### Fixed

- `url` claims (e.g. "deployed to https://…") crashed `audit`/`report`
  with a `NameError` — `_verify_url` was referenced but never imported.
  Verification now routes through `verify_claims`.

### Changed

- `llms.txt` no longer states a hard-coded ecosystem size; the registry owns the count.

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
