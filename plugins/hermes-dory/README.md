# Hermes Dory Provider

This package is the Hermes-facing Dory external memory provider.

Current state:

- Hermes `MemoryProvider`-compatible plugin class
- plugin manifest at `plugin.yaml`
- config loading from environment, `~/.hermes/dory.yaml`, or `~/.hermes/config.yaml`
- provider lifecycle hooks for prefetch, turn sync, session-end ingest, and date-partitioned built-in memory mirroring
- semantic `memory_write(...)` for first-class remember/update/forget actions
- exact-path `write(...)` for controlled file writes, hash-guarded replaces, and dry-run previews
- `research(...)` and guarded `purge(...)` support for the finalized Dory HTTP/MCP surface
- structured tool errors with `error_type` and HTTP `status_code` when Dory rejects a request

Recommended model:

- keep Hermes built-in memory short and stable
- use this provider for durable cross-session memory
- let Dory session ingest and nightly digestion handle heavier recall and promotion

## Config

Environment variables:

- `DORY_HTTP_URL`
- `DORY_HTTP_TOKEN`
- `DORY_HERMES_AGENT`
- `DORY_HERMES_MEMORY_MODE`
- `DORY_HERMES_WAKE_BUDGET_TOKENS`
- `DORY_HERMES_WAKE_PROFILE`
- `DORY_HERMES_WAKE_RECENT_SESSIONS`
- `DORY_HERMES_WAKE_INCLUDE_PINNED_DECISIONS`
- `DORY_HERMES_ACTIVE_MEMORY_INCLUDE_WAKE`
- `DORY_HERMES_SEARCH_K`
- `DORY_HERMES_SEARCH_MODE`

Hermes main config:

```yaml
memory:
  provider: dory
```

Provider-native config:

- preferred path: `~/.hermes/dory.yaml`
- fallback path: `~/.hermes/config.yaml`
- see `config.example.yaml` in this directory

Config resolution order:

1. Environment variables provide defaults.
2. The first discovered provider/main config file overrides those defaults value-by-value.
3. If no config file exists, the provider runs from environment/default values only.

Config file candidates are checked in this order:

1. `$HERMES_HOME/dory.yaml`
2. `$HERMES_HOME/dory.yml`
3. `$HERMES_HOME/dory/config.yaml`
4. `$HERMES_HOME/config.yaml`
5. `$HERMES_HOME/config.yml`
6. `~/.hermes/dory.yaml`
7. `~/.hermes/dory.yml`
8. `~/.hermes/dory/config.yaml`
9. `~/.hermes/config.yaml`
10. `~/.hermes/config.yml`

Search mode notes:

- accepted provider values: `hybrid`, `recall`, `bm25`, `text`, `keyword`, `vector`, `exact`
- legacy compatibility values still accepted: `lexical`, `semantic`
- legacy values are normalized before the HTTP request:
  - `text`, `keyword`, `lexical` -> `bm25`
  - `semantic` -> `vector`

Wake/active-memory notes:

- default `wake_profile` is `coding`, because Hermes is normally used for project work
- set `active_memory_include_wake: false` when Hermes already calls wake during prefetch to avoid duplicate context

Write safety notes:

- `memory_write` supports `dry_run`, `force_inbox`, and `allow_canonical`
- semantic memory-write actions are `write`, `replace`, and `forget`; legacy built-in Hermes `add`/`remove` hooks are normalized before they hit Dory
- `write` is the safer exact-path API when the target path is known; use `dry_run` and `expected_hash` for replace/forget flows
- `purge` defaults to dry-run and live purge requires an explicit reason plus the right guard flags
- Hermes built-in memory events mirror to `inbox/hermes-memory-mirror/YYYY-MM-DD.md`, not one unbounded file

Memory mode notes:

- `hybrid`: prefetch context and expose Dory tools
- `context`: prefetch context only
- `tools`: Dory tools only, no auto-injected context

## Install

Copy this folder into Hermes' memory plugin directory and enable it:

1. Place `plugins/hermes-dory/` under Hermes as `plugins/memory/dory/`
2. Set `memory.provider: dory` in `~/.hermes/config.yaml`
3. Create `~/.hermes/dory.yaml` from `config.example.yaml`
4. Restart Hermes or rerun `hermes memory setup`

## Boundary

This now matches Hermes' provider package shape and lifecycle surface. It still needs live verification against a real Hermes install before calling it fully production-ready.
