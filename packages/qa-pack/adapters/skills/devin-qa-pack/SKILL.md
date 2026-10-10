---
name: devin-qa-pack
description: "Verify what an agent session claimed it did (tests run, commits, files written, pushes) against its own tool-call evidence — before asserting 'I did X'. Read-only audit; verdicts PASS / PARTIAL / UNVERIFIED per session."
triggers: [model, user]
allowed-tools:
  - exec
  - read
---

# devin-qa-pack

Before saying "I ran the tests" or "the commit landed", verify the claim
against the session's own evidence — never assert what the audit cannot
confirm:

```bash
devin-qa-pack audit --session <id> --json
devin-qa-pack audit --all --limit 20 --json
```

Or, when this plugin's MCP server is connected, call `qa_audit` with the
same arguments — it returns the same JSON payload.

## Reading the result

- `sessions[].verdict` is `PASS` (every claim verified), `PARTIAL` (a
  dispute or a mix — read `claims[]` to see which failed) or
  `UNVERIFIED` (no claims, or none checkable — the session may still be
  fine, the evidence just is not there).
- Each claim in `sessions[].claims[]` carries `kind`, `detail`,
  `status` (`verified`/`disputed`/`unverifiable`) and `evidence`.
- `error` in the payload means the audit could not run (`no_store`,
  `bad_store`, `unknown_session`, `mcp`) — report that, do not guess.

## Rules

- Read-only by design. There is nothing to apply or fix — the tool
  produces verdicts, not changes.
- `--session` accepts an exact id or a unique prefix. `--source mcp`
  audits a cloud session through the hosted Devin MCP and needs
  `DEVIN_API_KEY`.
- Only affirm what the report marks `verified`. A `disputed` claim is a
  finding to surface to the user, not a detail to paper over.
