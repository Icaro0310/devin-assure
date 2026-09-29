# STATUS — devin-qa-pack

Updated: 2026-09-29 · Milestone: **M1 (done)** · Version: 0.1.0

## Done in M1

- `src/devin_qa_pack/`:
  - `claims.py` — extracts deliverable claims from `message_nodes`
    (`tests` + runner · `commit` + hash · `file` + path · `push`);
    agent-role messages only, dedup by `(kind, detail)`, defensive decode
    of the unstable `chat_message` format.
  - `verify.py` — resolves claims against `tool_call_state` ground truth
    (parsed via devin-internals-spec `SessionsStore`) plus `git
    cat-file`/existence checks when `working_directory` is on disk:
    `verified` / `disputed` / `unverifiable`.
  - `report.py` — per-session verdicts `PASS` / `PARTIAL` / `UNVERIFIED`,
    findings, JSON payload + text rendering.
  - `cli.py` (thin) — `audit --sessions-db <path> --session <id|prefix>`,
    `audit --all [--limit N]`, `--json`, auto-detects the default store.
    Exit codes: 0 = all `PASS` · 1 = ran, some `PARTIAL`/`UNVERIFIED` ·
    2 = could not run.
  - `paths.py` — platform data-dir detection (mirrors devin-doctor).
- `docs/SPEC.md` (canonical EN) + `SPEC.pt-BR.md` — claim→status
  semantics table, JSON contract, exit codes.
- Real READMEs (EN/PT-BR): problem / prior art / Devin-native extra /
  limitations / install.
- Fixtures-first tests: `create_sessions_db()` extended with controlled
  claim + tool-call rows (incl. NULL-payload row for the unreadable
  ground-truth path) and a real tmp `git` repo. **45 tests, all green**
  (Windows, Python 3.11.9, pytest 9.1.1). CLI verified end-to-end on a
  fixture db.

## Environment notes

- `python` = 3.11.9 w/ pytest 9.1.1; always `python -m pip`.
- Editable install needed `--no-deps --no-build-isolation` locally —
  `devin-internals-spec` was already installed (0.2.0, site-packages);
  `pyproject.toml` pins the git tag `v0.2.0` for real installs.
- exec runs under **cmd.exe**: no heredocs, no multi-line `python -c`;
  commit messages need repeated `-m` flags.

## Decisions / notes

- Status semantics: action claims (`tests`/`push`/`commit`) with no
  corroborating tool call → `disputed` (the action would have left a
  record); `file` claims without disk or tool evidence → `unverifiable`;
  undecodable tool-call rows → `unverifiable`, never `disputed` (absence
  of evidence ≠ evidence of absence when the record itself is opaque).
- Only agent-role messages are scanned for claims — a user quoting
  "tests passed" is not a delivery claim.
- All-disputed sessions report `PARTIAL` (the verdict enum has no FAIL;
  findings carry the detail). Documented in SPEC.md §5.

## Remaining for M2 (per kickoff)

1. `git fsck`/`reflog` cross-check for commit claims beyond `cat-file`.
2. Cost-per-claim stats (tokens/ACUs per verified claim — needs a cost
   data source; `cogs_json` is still marked unstable).
3. GitHub Action mode — audit in CI, comment the verdict on the PR.
4. PyPI publish (`pipx install devin-qa-pack`) — name + account needed.

## Blockers

None.
