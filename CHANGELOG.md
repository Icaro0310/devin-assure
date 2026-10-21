# Changelog

## Unreleased

- **Docs** — `README.corporate-windows.md` gains three recipe sections:
  *Read-only operation* (every command reads local Devin data only; a
  copied `--sessions-db` gives a hard no-touch guarantee), *What cannot
  be delegated* (local-only is posture, not enforcement — the opt-in
  `--online`/`--source mcp` flags exist and stay unused), and *Reporting
  false positives* (reports are not auto-redacted — excerpt the verdict
  block, pass it through `devin-redact` when installed, verify before
  sharing). `README.md` documents the same read-only guarantee for all
  platforms.
- **Action** — the published root `action.yml` ("devin-qa-pack audit")
  installs the pack from `github.action_path` (the checked-out revision
  under test) instead of `git+https://…@${{ github.sha }}`: inside a
  composite action `github.sha` is the *caller* repo's commit, so the
  old install line failed for every external consumer. The audit step
  now forwards `sessions-db` as `--sessions-db` (the flag the CLI
  declares — the previous `--db` aborted with an argparse error
  whenever a db path was passed) and builds argv with a bash array, so
  db paths containing spaces reach the CLI as a single element.
  `packages/qa-pack` gains `tests/test_action_contract.py`, pinning the
  install source, env-var-only input passing, the env→argv wiring and
  the audit gate's exit codes (2 fail-closed on an empty scope or
  unknown session, 1 on any non-PASS verdict, 0 when all pass).
- **CI** — `pr-approve` caller bumped from v1.5 to v1.6 in
  `auto-approve.yml` (the v1.6 gate adds cross-repo fix anchors and
  gate-eligible concurrency); `test_auto_approve_workflow.py` now pins
  the gate version exactly so a silent downgrade is caught.
- **Feature** — `devin-metrics commits`: session→commit correlation.
  40-hex SHAs and `/commit/<sha>` repo URLs are extracted from
  `tool_call_state` payloads via
  `devin_internals.commits.commit_references` (devin-internals-spec); the
  report lists each session's commits (short SHA + repo) plus a per-repo
  rollup, `--json` supported, and `summary` gains "Sessions with
  commits" / "Unique commit SHAs" rows.
- **Docs** — root README embeds the agent-assurance demo gif under the
  top badge block; the qa-pack package README demo embed now uses
  absolute URLs so it renders on the PyPI project page.
- **Feature** — `devin-qa-pack session-end` now appends one compact JSON
  line per verdict to `verdicts.jsonl` next to the side file
  (`<data-dir>/qa/verdicts.jsonl` by default; an `--out` override
  relocates both together). Records carry `session_id`, `verdict`,
  `audited_at`, claim counts and the skip `reason` — a machine-readable
  per-session ledger for dashboards and the `qa.session-end` trigger.
  The append is best-effort: the side file remains the contract.
- **CI** — `ci.yml` calls the reusable `adapters-check` sensor (`devin-powerups` `@v1`): scans distributed adapter/skill files for personal paths, internal names, token shapes and SKILL.md command coherence.
- **Feature** — `devin-metrics digest --weekly` composes the existing
  `summary`/`daily`/`watch` outputs into one markdown report
  (`digest-YYYY-Www.md`, atomic tmp+replace write) and prints its path.
  Incremental via the shared `devin_internals.cursor` state
  (`metrics-digest-state.json`): the last digested ISO week is the
  watermark, so a second run in the same week reports "already digested"
  and writes nothing unless `--force`; `--since` backfills an explicit
  week (`YYYY-Www`, a date inside it, or epoch ms), corrupt state fails
  open into a full run, and `DEVIN_CURSOR_DIR` overrides the `.devin/`
  anchor. The watch-guard findings moved to
  `devin_metrics.aggregate.watch_report` (shared with `watch`), and
  `devin-metrics` now requires `devin-internals-spec>=0.3.1`.
- **Docs** — corporate-Windows guides: Zscaler root export now covers both
  certificate stores (`certmgr.msc`/`certlm.msc`,
  `Cert:\CurrentUser\Root` + `Cert:\LocalMachine\Root`) and yields PEM
  (Base-64 wizard export, or `certutil -encode` after
  `Export-Certificate`, which writes DER).
- **Feature** — repo root now ships `.pre-commit-hooks.yaml` with the
  `devin-qa-pack-gate` hook (`devin-qa-pack audit --all --limit 1 --within .`),
  a read-only, offline commit gate that re-audits the newest Devin session
  for the repo being committed; `audit` gained `--within PATH` to scope
  `--all` by session `working_directory`, and an empty audit scope now
  fails closed (exit 2) instead of passing on zero evidence.
- **Docs** — refreshed the generated `Part of the DEVIN ecosystem` block: journey recuration v2 (six paths, zero repeats, `Local-first ops` label, `devin-bridge` in DevOps).
- **Docs** — ecosystem journey recuration applied (six curated audiences); stale `Path:` line removed from the devin-qa-pack eco-block, which is not a registry entry.
- **CI** — Ruff lint job added (`astral-sh/ruff-action`, pinned); codebase
  now lints clean.
- **Publish** — consolidated `pypi-publish.yml` builds and uploads
  `devin-evals`, `devin-metrics` and `devin-qa-pack` via PyPI Trusted
  Publishing (OIDC), tag `*-v*` or manual dispatch.
- **Fix** — `devin-qa-pack` CLI JSON report path restored
  (`_db`/`db` regression caught by the test suite).

## 2026-10 (F4 consolidation)

- Packages consolidated into this repo:
  `devin-evals` 0.2.0, `devin-metrics` 0.2.0, `devin-qa-pack` 0.1.0.
- Prior per-repo history lives in each package's git history.
