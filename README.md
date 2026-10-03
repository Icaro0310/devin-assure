<div align="center">

<img src="assets/banner.svg" alt="devin-qa-pack" width="100%"/>

<a href="https://github.com/Icaro0310/devin-qa-pack/actions/workflows/tests.yml"><img src="https://github.com/Icaro0310/devin-qa-pack/actions/workflows/tests.yml/badge.svg" alt="tests"/></a>


</div>

# devin-qa-pack

> **Unofficial community project.** Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.

**[Português (BR)](README.pt-BR.md)** · English

QA audit for Devin sessions: checks that what a session *claims* it
delivered is backed by what its tool calls *actually did* — tests run,
commits created, files written, work pushed — and prints a verdict per
session: `PASS` / `PARTIAL` / `UNVERIFIED`.

## The problem

Devin sessions end with the agent saying "tests passed", "committed
`a1b2c3d`", "pushed to origin". Those are text claims — a model can write
them whether or not the actions happened. Verifying them today means
scrolling the transcript by hand or trusting the summary. Teams that
adopt agent workflows need a cheap, repeatable way to answer *did this
session actually do what it claims?*

## Prior art

Transcript/session viewers (including Devin's own UI) show *what
happened*; CI status checks verify outcomes after the fact; neither
cross-checks the agent's delivery claims against recorded actions. The
general idea — compare declared intent with observed behavior — is old
(manifest-vs-manifest audits, `git fsck`, attestations like SLSA
provenance). What didn't exist: applying it to an agent's claims vs. its
own persisted tool-call log.

## What makes it Devin-native

Devin persists every tool call in `sessions.db → tool_call_state`. This
tool reads that table via
[devin-internals-spec](https://github.com/Icaro0310/devin-internals-spec)
and treats it as **ground truth**: a "tests passed" claim must be backed
by a run/execute call that ran a test runner and completed; "committed
`sha`" must show the hash in a call or in `git log`. Text-only auditors
*cannot* do this — remove Devin's store and the check disappears.

## Install

Python ≥ 3.10 and `pipx` are required. **Windows (PowerShell):** install `pipx` with `py -m pip install --user pipx`, run `py -m pipx ensurepath`, then reopen the terminal. **Linux (Debian/Ubuntu):** run `sudo apt install pipx python3-venv` and `pipx ensurepath`; reopen the terminal. Other Linux distributions should install `pipx` using their package manager.

```bash
pipx install "devin-qa-pack @ git+https://github.com/Icaro0310/devin-qa-pack.git"
```

(PyPI publication is on the M2 roadmap.)

## Usage

```bash
# audit one session (exact id or unique prefix)
devin-qa-pack audit --session <id> --sessions-db path/to/sessions.db

# audit everything, most recent first
devin-qa-pack audit --all --limit 20 --json
```

`--sessions-db` may be omitted. It auto-detects
`%APPDATA%/devin/cli/sessions.db` on Windows and
`$XDG_DATA_HOME/devin/cli/sessions.db` on Linux (default
`~/.local/share/devin/cli/sessions.db`). Always read-only.

Exit codes: `0` every session `PASS` · `1` some session
`PARTIAL`/`UNVERIFIED` · `2` audit could not run.

## Works with Devin alone (Devin-only mode)

devin-qa-pack is an offline, read-only audit of recorded Devin sessions. It
never calls an LLM, never touches the network, and never writes to Devin's
stores — a safe pick for restricted machines.

## Platform support

Tested on **Windows and Linux** (`windows-latest` + `ubuntu-latest` in CI).
The CLI session DB is auto-detected from `%APPDATA%/devin/cli/sessions.db`
on Windows and `$XDG_DATA_HOME/devin/cli/sessions.db` on Linux (default
`~/.local/share/devin/cli/sessions.db`). A legacy `~/.config/devin` layout is
also checked. macOS uses `~/Library/Application Support/devin/`. Pass
`--sessions-db` to override.

## Limitations

- `chat_message` and `tool_call_*_json` payloads are **unstable** formats
  (see devin-internals-spec SCHEMA.md). Decoding is defensive; rows that
  can't be read resolve claims to `unverifiable`, never to `disputed`.
- Claim extraction is heuristic: it looks for delivery phrasing
  ("tests passed", "committed <sha>", "created <path>", "pushed").
  Claims phrased differently are not extracted — the audit then reports
  `UNVERIFIED`, not a false `PASS`.
- File/commit checks use `sessions.working_directory` only when it
  exists on disk and is a git repo — otherwise they rely on tool-call
  evidence alone.
- It verifies *that* actions happened, not that the work is good.
  Green tests in a tool call don't prove the fix is correct.
- Read-only, offline; no real-time monitoring, no MCP server (M2).

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

## License

MIT — see [LICENSE](LICENSE).


---

If this saved you debugging time, a ⭐ on the repo helps others find it.
