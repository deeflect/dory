---
title: Retrieval observability warnings
status: active
updated: 2026-04-24
---

# Retrieval observability warnings

Agents should surface structured retrieval warnings instead of silently hiding ambiguity.

Important synthetic warning names are `planner_skipped`, `rerank_unavailable`, `session_fallback_used`, and `scope_filter_skipped`.

A good recovery answer explains whether planner, rerank, fallback, or session scope was used, skipped, or unavailable.
