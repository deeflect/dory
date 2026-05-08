---
title: Proposal safety reference
status: active
updated: 2026-04-26
---

# Proposal safety reference

When a semantic write target is ambiguous, the agent should create a proposal instead of writing directly to a canonical page.

The recovery message should include `ambiguous_subject`, list candidate subjects, and ask for confirmation before applying the proposal.

This protects public and private corpora from brittle heuristic writes.
