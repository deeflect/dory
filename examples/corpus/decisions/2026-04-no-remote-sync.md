---
title: Remote sync default decision
status: active
updated: 2026-04-19
---

# Remote sync default decision

Decision: do not enable remote sync by default for the public demo memory daemon.

Rationale: Dory's default posture is local-first. Remote sync can be useful, but it must stay explicit opt-in so agents do not accidentally move synthetic or private memory outside the selected corpus boundary.

Agents may mention remote sync only as an optional integration after confirming the operator has enabled it.
