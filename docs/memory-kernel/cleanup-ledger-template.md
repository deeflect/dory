---
title: Memory Cleanup Ledger Template
status: active
type: template
created: 2026-06-08
scope: public-safe
---

# Memory Cleanup Ledger Template

Use this template for reviewable cleanup ledgers under `inbox/maintenance/` in a
private corpus. The public repo keeps only the template.

```yaml
---
title: Memory cleanup ledger
type: maintenance-ledger
status: active
scope: private
updated: YYYY-MM-DD
---

## Queue
- id: cleanup-YYYYMMDD-001
  target: core/active.md
  issue: stale current claim
  evidence: inbox/maintenance/wiki-health.json
  action: replace-current-summary
  owner: operator
  status: pending
  rollback: restore previous hash from dory_get

## Completed
- id: cleanup-YYYYMMDD-000
  target: projects/example/state.md
  issue: outdated generated summary
  evidence: inbox/maintenance/example.json
  action: replaced summary with current canonical state
  owner: operator
  status: done
  rollback: previous hash recorded in review note
```

Rules:

- Prefer demotion, replacement, or supersession over deletion.
- Preserve historical evidence unless it is a true duplicate, test artifact, or
  sensitive leak.
- Every cleanup item needs evidence and rollback notes.
- Run public-safety checks before committing docs, fixtures, or examples.
