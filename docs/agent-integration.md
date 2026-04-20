# Agent integration

How to wire Claude Code, Codex CLI, opencode, OpenClaw, Hermes, or any other agent into Dory.

Dory is a shared memory service, not a per-agent library. Run one daemon over a markdown corpus, then point each agent frontend at the same HTTP or MCP surface. Deployment URLs, corpus paths, and auth tokens are environment-specific and stay outside this repo.

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

1. Call `dory_wake` at session start or task switch. Use `profile="coding"` for project work, `profile="writing"` for voice/content work, `profile="privacy"` for boundary-sensitive questions.
2. Use `dory_search` before any factual claim about projects, people, priorities, decisions, or current environment.
3. Use `dory_get` on exact result paths before quoting or acting on memory.
4. Use `dory_link` only when relationships or backlinks matter.
5. Use `dory_active_memory(include_wake=false)` when wake was already called and the reply needs task-specific context.

Treat wake as framing, not proof that every canonical file was loaded. Search and get are the authoritative read path.

## Write policy

Write only when at least one of these holds:

- the user explicitly says *remember*, *save*, or *update*
- a durable decision was made
- project state materially changed
- a durable people/project/current-truth fact was established

Use `dry_run=true` first when the route isn't obvious. Inspect `target_path`, `subject_ref`, and `message` before committing.

Prefer `dory_memory_write` for durable semantic writes. Keep subjects specific — a generic subject can resolve into an existing canonical page. Live semantic writes to canonical targets require `allow_canonical=true` after preview.

Use `force_inbox=true` for tentative or review-needed material. Use `dory_write` only when you know the exact target path and have read the current hash with `dory_get`; replace/forget require `expected_hash`.

`forget` retires memory while preserving audit history. Reserve `dory_purge` for exact generated/test/scratch cleanup — live purge requires a reason and matching `expected_hash`.

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

The bridge reads `DORY_HTTP_URL` and `DORY_CLIENT_AUTH_TOKEN` from the environment. Its fallback is `http://127.0.0.1:8766`, so remote deployments must set the URL explicitly.

Restart open agent sessions after tool-schema changes — MCP hosts often cache schemas for the life of the process. New sessions pull the live schema from `/v1/tools`.

## Per-agent installer

From the repo root:

```bash
DORY_HTTP_URL=http://127.0.0.1:8766 ./scripts/agent-policy/install.sh
```

Registers the HTTP-backed MCP bridge and inserts `scripts/agent-policy/dory-policy.md` into supported agent rule files. Idempotent. Flags: `--dry-run`, `--skip-claude`, `--skip-codex`, `--skip-opencode`.

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

This repo ships public-safe code, examples, and synthetic evals. Real corpus data, private eval questions, run artifacts, deployment domains, tokens, and machine-specific paths stay outside the public tree.
