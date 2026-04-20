# OpenClaw Dory Memory Plugin

This package is the native OpenClaw-facing memory plugin for using Dory as the active `plugins.slots.memory` backend.

Current state:

- OpenClaw plugin manifest at `openclaw.plugin.json`
- SDK entrypoint via `definePluginEntry(...)`
- Dory-backed `MemorySearchManager`
- `promptBuilder` that uses Dory `wake`
- compiled external-loader entry at `dist/index.js`
- `memory_search`, `memory_get`, and semantic `memory_write` tools registered into the OpenClaw runtime
- non-null Dory-backed `flushPlanResolver`
- recall-event emission into Dory via `/v1/recall-event`
- recall-driven promotion candidates surfaced to Dory dreaming jobs
- public artifact listing via `/v1/public-artifacts`
- richer Dory/OpenClaw diagnostics via `/v1/status`

Still not included:

- builtin/local fallback backend parity
- context-engine replacement
- live runtime verification against a local OpenClaw install
- migration/import of legacy OpenClaw workspace memory

## Expected config

- plugin config `baseUrl`
- optional plugin config `token`

## Activation shape

```json
{
  "plugins": {
    "slots": {
      "memory": "dory-memory"
    },
    "entries": {
      "memory-core": { "enabled": false },
      "dory-memory": {
        "enabled": true,
        "config": {
          "baseUrl": "http://127.0.0.1:8766"
        }
      }
    }
  }
}
```

The exported default entrypoint uses the documented OpenClaw plugin SDK shape:

- `package.json` `openclaw.extensions` declares `./dist/index.js`
- `openclaw.plugin.json` carries pre-runtime metadata plus `configSchema`
- runtime code exports `definePluginEntry({ id, kind, configSchema, register(api) })`
- `register(api)` resolves `plugins.entries.dory-memory.config.baseUrl` and registers the Dory-backed memory capability

`memory_write` is semantic-first: the agent supplies `action`, `kind`, `subject`, and `content`, and Dory resolves the canonical target internally. The old path-first Dory `/v1/write` surface is not the primary OpenClaw write path anymore.

Compaction flush is now Dory-backed as well: the plugin advertises a real flush plan so OpenClaw can prepare durable memory summaries before compaction, and those summaries are intended to flow through semantic memory writes instead of markdown-path mutation.

## Build

```bash
cd packages/openclaw-dory
npm install
npm run build
```

External OpenClaw installs should point at the package root. OpenClaw discovers the runtime entry from `package.json` `openclaw.extensions`, not from `openclaw.plugin.json`.

## Boundary

This package now implements the core OpenClaw memory contract in-repo, including flush planning, recall tracking, recall-driven promotion signals, public artifacts, and richer diagnostics. The largest remaining parity gap is builtin fallback behavior when Dory is unavailable.
