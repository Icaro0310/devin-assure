# KICKOFF M1 — devin-qa-pack

You are the dedicated session for THIS repository. Scaffold from the
ecosystem template — fill with real content. Rules: `docs/SPEC.md` EN
canonical, bilingual READMEs (problem / prior art / Devin-native extra /
limitations / install), logic in `src/devin_qa_pack/` + thin `cli.py`,
small commits + Devin trailer, `git push`, STATUS.md + CHANGELOG.md.

## One sentence

A QA pack for Devin workflows: validates that sessions actually delivered
what they claimed — checks that referenced files exist, tests were run,
commits were pushed — and produces an audit verdict per session.

## Devin-native differentiator

Reads `tool_call_state` via `devin-internals-spec` to verify *what tools
actually ran* — e.g. a session claiming "tests green" must show a
`run`/`terminal` call whose command contains `pytest`, with success status.
Competitor tools check text claims; this checks tool-call ground truth.

Dependency:
```toml
"devin-internals-spec @ git+https://github.com/Icaro0310/devin-internals-spec.git@v0.2.0",
```

## Scope (M1)

`src/devin_qa_pack/`:
- `claims.py` — extract deliverable claims from message_nodes (commit
  hashes mentioned, "tests passed", file paths claimed changed).
- `verify.py` — cross-checks per claim against tool_call_state ground
  truth + git log (if repo cwd on disk): claim->verified/disputed/unverifiable.
- `report.py` — verdict per session: PASS / PARTIAL / UNVERIFIED + findings.

## CLI

- `devin-qa-pack audit --sessions-db <db> --session <id>` — one session.
- `devin-qa-pack audit --all [--limit N] [--json]` — batch.
- Auto-detect default store; read-only; `--json` everywhere.

## Fixtures/tests

Use `devin_internals.fixtures.create_sessions_db()` + extend rows with
message_nodes containing claims and tool_call_state rows that confirm or
contradict them. Tests: verified claim, disputed claim (no tool call),
unverifiable claim, JSON shape, exit codes.

## Env notes

`python`=3.11.9; `python -m pip` only; no multi-line `python -c`; Windows.

## Done

Tests green · CLI on fixture · docs real · pushed. M2 queue in STATUS.md:
git fsck cross-check, cost-per-claim stats, GitHub Action mode, PyPI.
