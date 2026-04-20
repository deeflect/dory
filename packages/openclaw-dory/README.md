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
- optional plugin config `tokenEnv` for reading the bearer token from a named environment variable

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

External OpenClaw installs should point at the package parent directory, not directly at `dist/index.js`. For this repo that means adding the absolute `packages` directory to OpenClaw's plugin load path; OpenClaw then discovers `packages/openclaw-dory/package.json` and loads `openclaw.extensions`.

## Setup With OpenClaw Gateway

Build the plugin:

```bash
cd packages/openclaw-dory
npm install
npm run build
```

Add the plugin package parent to OpenClaw's load path:

```bash
openclaw config set plugins.load.paths '["/absolute/path/to/packages"]' --strict-json
```

Enable Dory memory and assign the memory slot:

```bash
openclaw config set plugins.entries.dory-memory.enabled true --strict-json
openclaw config set plugins.entries.dory-memory.config.baseUrl '"https://dory.example.com"' --strict-json
openclaw config set plugins.entries.dory-memory.config.token '"YOUR_DORY_TOKEN"' --strict-json

openclaw config set plugins.entries.memory-core.enabled false --strict-json
openclaw config set plugins.slots.memory '"dory-memory"' --strict-json
```

If your OpenClaw environment can provide secret refs or service environment variables, prefer that over plaintext token storage. This plugin also accepts `tokenEnv`:

```bash
openclaw config set plugins.entries.dory-memory.config.tokenEnv '"DORY_CLIENT_AUTH_TOKEN"' --strict-json
```

The managed gateway process must actually receive that environment variable. If it does not, plugin load will fail with a clear `tokenEnv ... empty or unset` error.

Validate and restart the managed gateway:

```bash
openclaw config validate
openclaw gateway restart
```

Do not run plain `openclaw gateway` when a launchd/system service is already installed and listening; use `openclaw gateway restart` to avoid a second foreground process colliding with the existing listener.

Verify through gateway-scoped checks:

```bash
openclaw gateway status
openclaw plugins inspect dory-memory
openclaw gateway call doctor.memory.status
openclaw gateway health
```

For OpenClaw versions where top-level `openclaw health` reports websocket abnormal-close errors while gateway checks pass, prefer `openclaw gateway health` and `openclaw gateway call doctor.memory.status` for this plugin.

Troubleshooting:

- If gateway startup reports an address or port conflict, the managed gateway service is probably already running.
- Only one provider should own `plugins.slots.memory`; disable `memory-core` when `dory-memory` is assigned.
- `openclaw plugins inspect dory-memory` should show the plugin loading from this package's `dist/index.js`.
- `openclaw gateway call doctor.memory.status` should report provider `dory-http` and embedding/vector availability from Dory.

## Boundary

This package now implements the core OpenClaw memory contract in-repo, including flush planning, recall tracking, recall-driven promotion signals, public artifacts, and richer diagnostics. The largest remaining parity gap is builtin fallback behavior when Dory is unavailable.
