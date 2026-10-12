# devin-qa-pack for VS Code

QA audits for Devin sessions from
[devin-qa-pack](https://pypi.org/project/devin-qa-pack/), inside the editor:
it verifies deliverable claims (tests, commits, files, pushes) against
`tool_call_state` ground truth.

This is a thin adapter: the extension runs `devin-qa-pack audit … --json`
and renders the result — no audit logic is reimplemented here. All verdicts
and claim evidence come from the CLI, so the editor surface can never drift
from what `devin-qa-pack` says on the command line. It exposes the same
read-only surface as the package's MCP server (`qa_audit`).

## Features

- Status bar item showing the worst verdict across audited sessions
  (`PASS (n)` / `PARTIAL` / `UNVERIFIED`), with the per-verdict counts in
  the tooltip. Click it to re-run the audit.
- **devin-qa-pack: Audit all sessions** — `audit --all --limit N --json`,
  then a quick-pick of verdicts; selecting one opens a markdown sheet with
  the session's claims and evidence.
- **devin-qa-pack: Audit this workspace's sessions** — same audit scoped
  to the open folder (`--within <workspaceFolder>`).
- **devin-qa-pack: Audit one session** — prompts for a session id (exact
  or unique prefix) and audits just that one (`--session <id>`).

## Read-only contract

`audit` never writes to the store it inspects — the CLI documents it as
read-only, and this adapter adds no flags that change that. Excluded on
purpose:

- `report` — writes a self-contained HTML file to disk.
- `session-end` — the SessionEnd hook handler; writes a JSON side file.
- `intent` — the QA-4 heuristic stays CLI-only for now (the MCP surface
  likewise exposes only `audit`).
- `--online` / `--allow-domain` — live network checks stay opt-in on the
  CLI; the adapter never passes them.

Exit codes are treated as results, not errors: 0 = all PASS, 1 = some
session PARTIAL/UNVERIFIED, 2 = the audit could not run (missing
sessions.db, unknown session) — the last surfaces the CLI's stderr.

## CLI compatibility (schema contract)

Pinned via the `devin-qa-pack-cli` field in `package.json`:
`devin-qa-pack >=0.1,<0.2` (the released CLI is 0.1.x).

The extension renders this `audit --json` shape:

```json
{ "sessions_db": "…", "sessions": [ { "session_id": "…", "title": "…",
  "working_directory": "…", "verdict": "PASS|PARTIAL|UNVERIFIED",
  "summary": { "verified": 0, "disputed": 0, "unverifiable": 0 },
  "claims": [ { "kind": "…", "detail": "…",
    "status": "verified|disputed|unverifiable", "evidence": "…",
    "node_id": 0, "excerpt": "…" } ] } ] }
```

Required per session: `session_id` (string), `verdict` (one of the three
values), `summary` (object) and `claims` (array). A payload missing
required fields surfaces an explicit drift error instead of a blank badge.

## Requirements

`devin-qa-pack` installed and on `PATH`, plus a Devin `sessions.db` it can
auto-detect:

```bash
uv tool install devin-qa-pack
```

or set `devinQaPack.executable` to an absolute path, and
`devinQaPack.extraArgs` to `["--sessions-db", "…"]` for a non-default store.

## Settings

| Setting | Default | Description |
|---|---|---|
| `devinQaPack.executable` | `devin-qa-pack` | CLI executable (name or absolute path) |
| `devinQaPack.limit` | `20` | Max sessions per `--all` audit |
| `devinQaPack.extraArgs` | `[]` | Extra args appended to every CLI call |

## Privacy

The extension runs the local CLI and shows its output. It makes no network
calls of its own; `--online` live URL checks are never enabled by the
adapter.

## License

MIT — see LICENSE.
