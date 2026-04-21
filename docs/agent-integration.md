# Agent integration

How to wire Claude Code, Codex CLI, opencode, OpenClaw, Hermes, or any other agent into Dory.

Dory is one shared memory service, not a per-agent library. Run the daemon over a markdown corpus, then point each agent at the same HTTP or MCP surface. Deployment URLs, corpus paths, and auth tokens are environment-specific and must stay outside the repo.

## Repo structure

Agent-facing files:

| Surface | Path | Purpose |
|---|---|---|
| Shared agent skills | `skills/dory-*/SKILL.md` | Portable wake/search/write/maintain/dream instructions for agents that support skill folders. |
| Shared policy snippet | `scripts/agent-policy/dory-policy.md` | The global rule block installed into Claude Code, Codex CLI, and opencode rule files. |
| Agent installer | `scripts/agent-policy/install.sh` | Idempotently registers the HTTP-backed MCP bridge, policy snippet, and Dory skill symlinks for Claude Code, Codex CLI, and opencode. |
| Claude/stdio bridge | `scripts/claude-code/dory-mcp-http-bridge.py` | Stdio MCP compatibility process that forwards tools to a running Dory HTTP server. |
| Claude example config | `scripts/claude-code/mcp.example.json` | Minimal HTTP-bridge MCP config. Replace URL/token for remote or TLS deployments. |
| Codex helper | `scripts/codex/dory` | CLI wrapper that defaults to `data/corpus` and `.dory/index` in this repo. |
| OpenClaw plugin | `packages/openclaw-dory/` | Native OpenClaw memory-slot plugin package. |
| Hermes plugin | `plugins/hermes-dory/` | Hermes external memory provider package. |
| Live tool schema | `GET /v1/tools` | HTTP-published MCP schema consumed by bridges and used as the contract source. |

Claude Code, Codex CLI, and opencode are rule and MCP clients. OpenClaw and Hermes are code-based clients configured through their own plugin/provider systems instead of the policy installer.

## Tool surface

| Tool | MCP | HTTP | CLI | When to use |
|---|:-:|:-:|:-:|---|
| `dory_wake` | ✓ | ✓ | ✓ | Session start or task switch — loads bounded hot context. |
| `dory_active_memory` | ✓ | ✓ | ✓ | High-stakes or ambiguous replies — staged, task-specific retrieval. |
| `dory_search` | ✓ | ✓ | ✓ | Find candidate sources. Use `mode="exact"` for unique markers. |
| `dory_get` | ✓ | ✓ | ✓ | Read exact paths and hashes after search. |
| `dory_link` | ✓ | ✓ | equivalents | Inspect neighbors, backlinks, or wikilink lint. CLI equivalents are `neighbors`, `backlinks`, and `lint`. |
| `dory_memory_write` | ✓ | ✓ | ✓ | Preferred semantic write — subject is resolved, not path-first. |
| `dory_write` | ✓ | ✓ | — | Exact-path markdown write when the target is known. |
| `dory_purge` | ✓ | ✓ | ✓ | Guarded hard-delete for scratch and generated artifacts. |
| `dory_status` | ✓ | ✓ | ✓ | Corpus, index, auth, and capability diagnostics. |
| `dory_research` | ✓ | ✓ | ✓ | Bounded multi-source research artifact generation. |

## Read loop

1. `dory_wake` at session start or task switch. Pick the profile: `coding` for project work (operational only), `writing` for voice/content work (voice-first), `privacy` for boundary-sensitive questions (boundary-only; do not treat it as a profile dump).
2. `dory_search` before any factual claim about projects, people, priorities, decisions, or current environment.
3. `dory_get` on exact result paths before quoting or acting. It reads paths inside the configured Dory corpus, not arbitrary repo files cited as external evidence.
4. `dory_link` only when relationships or backlinks matter. Bound dense project/core queries with `max_edges`, and drop noisy families like `logs/sessions/` via `exclude_prefixes`. Responses include `count`, `total_count`, and `truncated` so you can tell when the graph was capped.
5. `dory_active_memory(profile="coding|writing|privacy|personal|general", include_wake=false)` when wake already ran and the reply needs task-specific retrieval. Prefer an explicit profile over `auto`. If you pass `include_wake=true`, active memory uses the profile's wake policy and avoids inlining unrelated personal context.

Treat wake as framing, not proof that every canonical file was loaded. Search + get is the authoritative read path.

Default search results include path, lines, snippet, `evidence_class`, `confidence`, and stale warnings. Debug-only internals (`score`, `score_normalized`, `rank_score`, and `frontmatter`) require `debug=true`; agents should trust the returned order instead of reading score fields. Prefer canonical/current evidence for current-state answers; treat `inbox`, `raw`, and `session` hits as supporting material unless the user explicitly asked for raw or recent material.

## Write policy

Write only when at least one of these is true:

- The user explicitly says *remember*, *save*, or *update*.
- A durable decision was made.
- Project state materially changed.
- A durable people, project, or current-truth fact was established.

Dry-run first when the route isn't obvious (`dry_run=true`). Inspect `target_path`, `subject_ref`, and `message` before committing.

**Prefer `dory_memory_write`** for durable semantic writes. Keep subjects specific — a generic subject can resolve into an existing canonical page. Canonical target previews are labeled `CANONICAL TARGET`; live writes to canonical targets need `allow_canonical=true` after preview. Use `force_inbox=true` for tentative or review-needed material.

**Use `dory_write` only when you know the exact path** and have read the current hash with `dory_get`. Kinds: `append`, `create`, `replace`, `forget`. New files need `frontmatter.title` and `frontmatter.type`; use `type: capture` for `inbox/**`. `replace` and `forget` require `expected_hash`; `forget` also requires a `reason`.

`forget` retires memory but keeps the audit trail. Reserve `dory_purge` for exact generated, test, or scratch cleanup. Live purge needs a reason and a matching `expected_hash`.

## HTTP setup

Start the server:

```bash
uv run dory-http --corpus-root <corpus> --index-root <index> --host 127.0.0.1 --port 8766
```

Client environment:

```bash
export DORY_HTTP_URL=http://127.0.0.1:8766
export DORY_CLIENT_AUTH_TOKEN="$(uv run dory auth new codex)"
```

When the server enforces tokens, clients must send:

```text
Authorization: Bearer <token>
```

Browser wiki login requires `DORY_WEB_PASSWORD`. Without it, the login form returns 503 by design.

## MCP setup

Native MCP:

```bash
uv run dory-mcp --mode stdio
```

HTTP-backed bridge for hosts expecting a stdio MCP process:

```bash
python3 scripts/claude-code/dory-mcp-http-bridge.py
```

The bridge reads `DORY_HTTP_URL` and `DORY_CLIENT_AUTH_TOKEN` from the environment. Default fallback is `http://127.0.0.1:8766`, so remote deployments need to set the URL explicitly.

By default the bridge runs one local session sync before `dory_wake`. That lets Claude see just-finished Codex/Claude/OpenClaw/Hermes sessions without waiting for the background shipper poll. Set `DORY_SYNC_SESSIONS_ON_WAKE=false` to skip this.

Restart open agent sessions after tool-schema changes because MCP hosts cache schemas per process. New sessions pull live schemas from `/v1/tools`.

## Per-agent installer

From the repo root:

```bash
DORY_HTTP_URL=http://127.0.0.1:8766 ./scripts/agent-policy/install.sh
```

Registers the HTTP-backed MCP bridge, inserts `scripts/agent-policy/dory-policy.md` into supported agent rule files, validates the bundled skills, and symlinks `skills/dory-*` into global skill folders. Idempotent.

Skill symlinks land in `~/.agents/skills`, `~/.claude/skills`, and `~/.codex/skills` when the corresponding agent section is enabled. Flags: `--dry-run`, `--skip-claude`, `--skip-codex`, `--skip-opencode`, `--skip-skills`.

OpenClaw lives under `packages/openclaw-dory/`. Hermes lives under `plugins/hermes-dory/`. They use the same HTTP daemon and bearer-token model, but neither is installed by `scripts/agent-policy/install.sh`.

## Direct HTTP examples

```bash
curl -X POST "$DORY_HTTP_URL/v1/wake" \
  -H "Authorization: Bearer $DORY_CLIENT_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"budget_tokens":1200,"profile":"coding","agent":"codex"}'

curl -X POST "$DORY_HTTP_URL/v1/search" \
  -H "Authorization: Bearer $DORY_CLIENT_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"active projects","k":5}'

curl -X POST "$DORY_HTTP_URL/v1/search" \
  -H "Authorization: Bearer $DORY_CLIENT_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"unique-marker","mode":"exact","k":5}'

curl -X POST "$DORY_HTTP_URL/v1/memory-write" \
  -H "Authorization: Bearer $DORY_CLIENT_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"write","kind":"note","subject":"example-project","content":"Temporary note.","dry_run":true}'

curl -X POST "$DORY_HTTP_URL/v1/memory-write" \
  -H "Authorization: Bearer $DORY_CLIENT_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"write","kind":"decision","subject":"example-project","content":"Ship by end of sprint.","allow_canonical":true}'
```

## Privacy boundary

This repo ships public-safe code, examples, and synthetic evals. Real corpus data, private eval questions, run artifacts, deployment domains, tokens, and machine-specific paths live outside the public tree.
