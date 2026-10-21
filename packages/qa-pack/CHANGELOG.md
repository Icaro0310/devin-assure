# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `delegation` claim kind: "delegated to a subagent/worker"-style
  assertions in agent messages (EN phrasings like "delegated the scan
  to a subagent" / "ran in parallel via subagent", PT like "deleguei" /
  "subagente") extract as `delegation` claims. They verify against
  spawn tool calls in `tool_call_state`: tool names carrying
  "subagent"/"sub_agent" or the `task`/`spawn_agent`/`delegate`-style
  aliases classify as `spawn` (checked across `kind`/`type`/`tool`/
  `name`/`toolName`/`tool_name`/`toolId` keys and "subagent" titles).
  A completed spawn verifies the claim, a failed spawn or no spawn
  call disputes it, unreadable ground truth stays unverifiable.

### Fixed

- `README.md` demo embed and its surrounding link used repo-relative
  paths, which do not resolve on the PyPI project page; they now use
  absolute `raw.githubusercontent.com`/`github.com` URLs.

## [0.2.1] - 2026-10-16

### Fixed

- `audit`/`intent`/`report` with a `--sessions-db` file that exists but
  is not a SQLite database crashed with an unhandled
  `sqlite3.DatabaseError` traceback (exit 1) while provider mode
  reported the same store as a warning + exit 2. `_open_store` now
  catches `sqlite3.Error`, so the fast path fails closed with
  `error: cannot open …` (exit 2) like every other unreadable store.

### Changed

- `pyproject.toml` description (the PyPI summary) still described 0.2.0
  as a Devin-`sessions.db`-only audit; it now names the multi-surface
  evidence layer (Devin CLI, Copilot CLI, Codex CLI, VS Code chat).

## [0.2.0] - 2026-10-16

### Added

- `evidence/` — a surface-agnostic evidence layer:
  `EvidenceMessage`/`EvidenceToolCall`/`SessionEvidence` normalize what
  an audit needs, and one read-only provider per store reads it.
  Providers: `devin-cli` (sessions.db via `SessionsStore`),
  `copilot-cli` (`session-state/<uuid>/events.jsonl` plus flat and
  `history-session-state` layouts under `$COPILOT_DIR`/`$COPILOT_HOME`/
  `~/.copilot`, `workspace.yaml` workspace seed), `codex-cli`
  (newest `~/.codex/state_<N>.sqlite` threads merged with
  `rollout-*.jsonl` under sessions/archived/`$CODEX_SESSIONS_DIR`) and
  `vscode-chat` (`chatSessions` `.json` snapshots + `.jsonl`
  mutation-log replay, workspace from `workspace.json` folder URI or a
  path-like decoded `<wsDir>`).
- `audit`/`report --surface {devin-cli,copilot-cli,codex-cli,vscode-chat,
  auto}` — default `auto` discovers every detected store when no
  `--sessions-db` is pinned; absent stores contribute nothing.
  Provider-mode session ids are namespaced `surface:native_id`;
  `--session` prefix-matches across surfaces or scopes with
  `surface:id`. `--within`/`--limit` apply to the merged,
  newest-first list. Reports and `audit_dict` carry a `surface` label;
  the HTML report shows it as a chip.
- The `qa_audit` MCP tool takes `surface` and fails closed
  (`no_sessions`) when a provider-mode query matches nothing, like the
  CLI's exit-2 rule.

### Changed

- `claims`/`verify`/`intent` consume the evidence model instead of raw
  `message_nodes`/`tool_call_state` rows — the `chat_message` JSON
  decode moved into the devin-cli provider; claim detection and verdict
  logic are unchanged (a golden test asserts identical verdicts through
  both paths). `Claim.node_id` cites the provider's real source id when
  available (devin `message_nodes.node_id`), else the message position.
- An explicit `--sessions-db` alone keeps the Devin fast path (bare
  session ids, no other surfaces queried) so existing automation sees
  identical output; combine it with `--surface` to widen scope.

### Fixed

- `vscode-chat` extracted exit status (`terminalCommandState.exitCode`,
  `resultDetails`, `isComplete`) now lands in
  `EvidenceToolCall.update_payload` — the canonical home per
  SESSION-EVIDENCE §4 — instead of inside the raw invocation `payload`.
- `devin-qa-pack-mcp` exits with a one-line install hint when the `mcp`
  extra is missing instead of dumping a traceback; startup
  `ImportError`s from `.run()` keep their traceback.


## [0.1.2] - 2026-10-15

### Changed


- Version bumped to 0.1.1 to restore the lockstep gate with the VS Code
  adapter (`adapters/vscode/package.json` shipped 0.1.1 while the
  package stayed 0.1.0 — the `test-qa-pack-vscode` lockstep check
  requires both to match).
- Docs: guias de plataforma regenerados (Zscaler, exceções de rede opt-in nomeadas); FAQ corrige `DEVIN_ORG_ID` para condicional (enviado apenas quando setado).
- README gains the generated `Part of the DEVIN ecosystem` block
  (track/nature/audience/interface rendered from the registry).

- `labeler.yml` is now a thin caller of the shared reusable workflow in `devin-powerups` (`@v1`); PR labeling behavior is unchanged.
- The inline `secrets-scan` job now calls the shared reusable workflow, which switches fixture handling from a blanket `fixtures` directory exclusion to hash-pinned `.secrets-scan-allow` entries.

- Install section now recommends pypi `uv tool install devin-qa-pack`; `install.sh` defaults to the PyPI package (`DEVIN_QA_PACK_REF` opts back into a git ref) as the primary route, with `pipx`/source installs documented as alternatives.
- `examples/agent-assurance` — session generation now runs
  `devin-evals dream unit` (`devin-dream` was absorbed into
  `devin-evals`); the evals git pin moved to the 0.2.0 commit, which
  also lets the devkit updater recognize the upgrade.

### Added

- `.pre-commit-hooks.yaml` at the repo root — `devin-qa-pack-gate` runs
  `devin-qa-pack audit --all --limit 1 --within .` on every commit:
  read-only, offline, scoped to the repo's own sessions; see
  "Pre-commit gate" in the README.
- `audit --within PATH` (with `--all`) — only audits sessions whose
  `working_directory` is PATH or inside it, applied before `--limit`.
  This is what lets the pre-commit gate audit *this* repo's newest
  session instead of whichever project's session is globally newest.
- Read-only adapters: `devin_qa_pack.mcp_server` MCP server (`qa_audit`,
  `devin-qa-pack-mcp` entry point, `mcp` extra), Devin skill and
  `adapters/` plugin root. `session_id` + `audit_all_sessions` together
  is a `conflicting_scope` error (CLI mutual exclusion); the default
  batch limit audits all sessions like the CLI (`limit=0` → unbounded).
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

- `audit --all` over a store with no matching sessions (empty db, or a
  `--within` scope nothing falls under) exited 0 — `all([])` is True —
  so a commit gate would pass on zero audited evidence. An empty audit
  set now exits 2 (`error: no sessions to audit`), failing closed.
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
