---
name: Audit false positive
about: Report a PASS/PARTIAL/UNVERIFIED verdict that looks wrong
title: "[audit] "
labels: ["bug", "false-positive"]
---

## Verdict reported

<!-- PASS, PARTIAL, UNVERIFIED or SKIPPED -->

## Claim extracted

<!-- Paste the claim text and type, sanitized. -->

## Evidence found or expected

<!-- Paste the relevant sanitized tool-call evidence or explain what should
     have matched. Remove session content, secrets, tokens and user paths. -->

## Command used

```bash
devin-qa-pack audit --session <id> --sessions-db <path>
```

## Why this looks wrong

## Sanitized reproduction

<!-- Attach a sanitized fixture, JSON output or minimal sessions.db if you can.
     Never attach real credentials or private session transcripts. -->
