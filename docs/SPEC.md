# SPEC — `devin-qa-pack` (M1)

Canonical English spec. PT-BR: `SPEC.pt-BR.md`.

## 1. Problem

Devin sessions end with the agent *saying* what it delivered: "tests
passed", "committed `a1b2c3d`", "created `src/report.html`", "pushed to
origin". Those are **text claims** — a model can write them whether or
not the actions actually happened. Today, verifying them means manually
scrolling the session transcript or re-running the work. Teams adopting
agent workflows have no cheap way to answer: *did this session actually
do what it claims?*

The actions an agent takes are already recorded — every tool call is
persisted in `tool_call_state`. Nothing cross-checks the claims against
that record.

## 2. Devin extra (and the 3 tests)

**Extra:** it verifies claims against `tool_call_state` *ground truth*
(read via `devin-internals-spec`) — not against other text. A session
claiming "tests green" must show a run/execute call whose command ran a
test runner and completed; "committed `sha`" must show the hash in a tool
call or in `git log` of the on-disk working directory.

- **Side-by-side:** transcript viewers and text-auditing tools check
  claims against *text*. They cannot distinguish "ran pytest and it
  passed" from "wrote that pytest passed". This tool checks the recorded
  tool calls — something no text checker can do at all.
- **No-Devin:** without Devin's `sessions.db`/`tool_call_state` there is
  no ground truth to check — the extra disappears.
- **One sentence:** *"It audits whether a Devin session's tool calls back
  up what the agent said it did."*

## 3. Scope

`src/devin_qa_pack/`:

- `claims.py` — extract deliverable claims from `message_nodes`:
  test-result claims (`tests`, with the runner name when stated),
  commit claims (`commit` + hash), file claims (`file` + path),
  push claims (`push`), HTTP-status claims (`http` + status code, e.g.
  "the API returned 200"). Agent-role messages only; claims deduplicated
  by `(kind, detail)`.
- `verify.py` — resolve each claim against `tool_call_state` ground
  truth, plus `git` when `sessions.working_directory` is a repo on disk:
  `verified` / `disputed` / `unverifiable`.
- `report.py` — one verdict per session: `PASS` / `PARTIAL` /
  `UNVERIFIED`, with per-claim findings.
- `html_report.py` — aggregate audits into one deterministic,
  self-contained static HTML file (inline CSS, zero JS).
- `cli.py` — thin wrapper (`audit`, `report`); `paths.py` — default
  store location.

## 4. Non-scope

- Does not judge code quality or whether the work is *correct* — only
  whether claims are corroborated.
- Does not run tests or builds itself.
- Does not write to any Devin store (read-only, always).
- Does not parse claim semantics beyond the listed kinds (e.g. "the fix
  handles edge cases" is not a checkable claim).
- No MCP server, no watch mode, no GitHub Action in M1 (M2 queue).

## 5. Claim → status semantics

Per claim kind, given the session's parsed tool calls:

| kind | verified | disputed | unverifiable |
|---|---|---|---|
| `tests` | an execute call ran the claimed runner (or any runner, for generic "tests passed") and completed | matching call failed, or no execute call ran a test | matching call never finished, or ground-truth rows unreadable |
| `push` | an execute call ran `git push` and completed | matching call failed, or none recorded | unreadable ground truth |
| `commit` | hash appears in a tool call's payloads, or `git cat-file -e <sha>^{commit}` succeeds in the on-disk repo | repo exists but lacks the hash; no call and no repo | unreadable ground truth |
| `file` | a tool call references the path, or the file exists under the on-disk working dir | matching call failed, or file absent under on-disk working dir | no disk path and no tool call to check |
| `http` | a tool-call payload/output records the claimed status code | recorded outputs show a different status code | no output records any status, or ground-truth rows unreadable |

"Unreadable ground truth" = `tool_call_state` rows exist but every
payload is NULL (interrupted call) or fails JSON decode — the
corroborating call may be inside them, so absence of evidence cannot be
treated as evidence of absence. By contrast, a session whose rows all
decode and contain no matching call gets `disputed`: the actions these
claims describe always go through tool calls.

Session verdict:

| verdict | meaning |
|---|---|
| `PASS` | ≥1 claim, all verified |
| `PARTIAL` | audit ran; at least one claim disputed or mixed outcomes (incl. fully disputed) |
| `UNVERIFIED` | no claims found, or every claim unverifiable |

## 6. Interfaces

| Interface | Description |
|---|---|
| **Library** `devin_qa_pack` | `claims.extract_claims` · `verify.verify_claim(s)` · `report.audit_session/audit_all` — frozen dataclasses in, statuses out |
| **CLI** `devin-qa-pack` | `audit --sessions-db <path> --session <id|prefix>` · `audit --all [--limit N]` · `report [--session <id|prefix>] [--limit N] --out <file>` (self-contained HTML) · `--json` on audit · auto-detects `<data dir>/cli/sessions.db` when `--sessions-db` omitted |
| **Docs** | `SPEC.md` (canonical) + `SPEC.pt-BR.md` |

CLI exit codes: `0` every audited session `PASS` · `1` audit ran, some
session `PARTIAL`/`UNVERIFIED` · `2` audit could not run (missing or
unreadable store, unknown/ambiguous session, bad usage).

### JSON contract

```json
{
  "sessions_db": "…/sessions.db",
  "sessions": [
    {
      "session_id": "…",
      "title": "…",
      "working_directory": "…",
      "verdict": "PASS|PARTIAL|UNVERIFIED",
      "summary": {"verified": 3, "disputed": 1, "unverifiable": 0},
      "claims": [
        {"kind": "tests", "detail": "pytest", "status": "verified",
         "evidence": "`python -m pytest -q` completed (tc-1)",
         "node_id": 2, "excerpt": "All tests passed — pytest is green…"}
      ]
    }
  ]
}
```

## 7. Fixtures and tests (TDD — fixtures first)

`tests/conftest.py` builds a `sessions.db` via
`devin_internals.fixtures.create_sessions_db()` and extends it with
controlled rows: message_nodes carrying claims and tool_call_state rows
that confirm, contradict, or leave claims unverifiable (including a NULL
payload row for the interrupted-call path). A real `git init` tmp repo
exercises the on-disk branch of `verify.py`.

Coverage: per-kind extraction (incl. user-role exclusion, dedup,
non-JSON payloads) · every verified/disputed/unverifiable path · verdict
computation · `--json` contract · CLI exit codes.

## 8. Risks and mitigation

| Risk | Mitigation |
|---|---|
| `chat_message` / `tool_call_*_json` formats are *unstable* (SCHEMA.md) | defensive decode: several field names, non-JSON falls back to raw text, undecodable rows → `unverifiable`, never a crash |
| Claim regexes false-positive | conservative patterns (commit claims need "commit" wording + hex; file claims need a write verb + `path.ext`); dedup; documented |
| Real store changes under us | `SessionsStore` gates on the schema detector; unknown versions fail loudly |
| Privacy | read-only; findings carry paths/hashes/excerpts (≤160 chars), never row dumps |
| `git` missing or `working_directory` not on disk | checks degrade to tool-call evidence only |

## 9. Definition of done

- [x] `claims.py`, `verify.py`, `report.py`, thin `cli.py` implemented
- [x] `devin-qa-pack audit` works on a fixture `sessions.db` (single +
      `--all` + `--json`)
- [x] Tests green on Windows (45) — fixtures-first
- [x] README (EN + PT-BR): problem / prior art / Devin-native extra /
      limitations / install
- [x] `STATUS.md` with M2 queue · `CHANGELOG.md` updated · pushed

## 10. M2 queue (from kickoff)

1. `git fsck`/`git reflog` cross-check for commit claims beyond `cat-file`.
2. Cost-per-claim stats (tokens/ACUs per verified claim — needs cost data
   source, likely `cogs_json` when its format stabilizes).
3. GitHub Action mode (audit sessions in CI, comment verdict on PR).
4. PyPI publish (`pipx install devin-qa-pack`).
