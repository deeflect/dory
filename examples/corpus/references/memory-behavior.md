---
title: Memory behavior regression notes
type: reference
status: active
source_kind: human
temperature: warm
---

# Memory behavior regression notes

For coding-agent tasks, Dory should provide a focused task brief from active
memory and keep raw search snippets available through tools instead of injecting
them automatically.

Current truth belongs in canonical project state, core active context, and
canonical decisions. Historical evidence belongs in session logs, digests,
archives, imported notes, and semantic evidence artifacts.

Generated digests are searchable historical evidence. They should not become
active-memory current truth unless a reviewed promotion creates or replaces an
active canonical claim. In short: generated digests should not become active-memory current truth automatically.

Hermes automatic prefetch should inject the active-memory brief first. It should
fall back to wake only when active memory is empty. The legacy raw evidence dump
is only for explicit debug workflows.

Honcho is a deferred experiment for peer or conversational modeling, not a
replacement for Dory's canonical markdown and claim-event source of truth.
Honcho is not a replacement for Dory as the canonical memory substrate.
