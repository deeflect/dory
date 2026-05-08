---
title: Agent recovery playbook
status: active
updated: 2026-04-23
---

# Agent recovery playbook

If eval validation reports `DORY_VALIDATION_MISSING_SOURCE`, the agent should run `uv run python eval/validate.py`, inspect the question's `expected_sources`, and fix the synthetic fixture or typo.

The agent must not satisfy a missing public source by copying from a private corpus. When the right source is unclear, write a blocker note instead of guessing.

For malformed YAML, fix the question file first, then rerun the validator before changing retrieval code.
