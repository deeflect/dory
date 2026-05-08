---
title: Session-style recall digest
status: active
updated: 2026-04-21
---

# Session-style recall digest

This public digest is a synthetic stand-in for scoped session recall. It is durable by design; it is not a raw private session log.

In session-style recall case `relay-4242`, Demo Agent Iris decided to retry HTTP 409 conflicts with exponential backoff.

The digest also records that agents must report warning `scope-filter-skipped` when a requested session key is absent from scope.
