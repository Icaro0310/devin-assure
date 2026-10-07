# Decisions

## 2026-10-07 — `action.yml` stays a distribution artifact; internal runs go through the PAS dispatcher

`action.yml` (composite action) remains published for external consumers who
want to audit sessions inside their own CI. It is intentionally **not** wired
into any workflow of this repo or the PAS: `session-end` audits Devin Desktop
sessions against the local `sessions.db`, which does not exist in CI runners.

Internal production path (as of 2026-10-07): the PAS trigger catalog invokes
the installed CLI via manifest `qa.session-end`
(`personal-agent-system/.devin/catalog/triggers/qa-session-end.yaml`) —
`SessionEnd` trigger with `SessionSweep` fallback, since native SessionEnd
rarely fires on this harness. Verdicts land in
`<data-dir>/qa/<session-id>.json` and are surfaced on devin-dashboard via
`laptop_reporter`.

If a real CI consumer ever appears, revisit; until then the action is
documentation + distribution, not dead weight.
