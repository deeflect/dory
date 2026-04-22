<div align="center">

<img src="https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExa2NjdHJ1NzBmbXc2d3N5eDYzMnh3MDg2YnA2Y2ZwdWQ3cXRydWpwaCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/CJywPvSt4JE0E/giphy.gif" alt="Dory — just keep swimming" width="480" />

# 🐟 Dory

**One memory layer. Every agent. Local by default.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Built with uv](https://img.shields.io/badge/built%20with-uv-orange)](https://docs.astral.sh/uv/)
[![MCP](https://img.shields.io/badge/MCP-native-purple)](https://modelcontextprotocol.io/)
[![Status](https://img.shields.io/badge/status-building-yellow)]()

*Your agent forgot who you are. Again. Dory fixes that.*

</div>

---

## The problem

Every AI agent you use keeps its own half-memory.

- Claude remembers one slice.
- Codex keeps another.
- opencode writes to yet another folder.
- OpenClaw and Hermes park sessions somewhere else entirely.
- The next model still asks what you're building, what you prefer, and what already happened.

You end up re-explaining yourself on loop. Decisions get lost. Project state goes stale. No memory actually follows you across tools.

## What Dory is

A **local-first memory daemon** that gives every agent the same brain.

Markdown is the source of truth. SQLite is a disposable sidecar. Agents read and write through a narrow API — `wake`, `search`, `get`, `memory-write`, `link` — so Claude, Codex, opencode, OpenClaw, Hermes, and anything with HTTP or MCP share one memory substrate while keeping their own personality.

> Dory isn't trying to make every agent identical. It's giving them the same memory so they can act like they share a brain.

## Quickstart

```bash
git clone <dory-repo-url>
cd dory
uv sync --frozen
mkdir -p data/corpus
export DORY_CORPUS_ROOT="$PWD/data/corpus"
export DORY_INDEX_ROOT="$PWD/.dory/index"
export DORY_AUTH_TOKENS_PATH="$PWD/.dory/auth-tokens.json"
uv run dory init
```

Try it:

```bash
uv run dory memory-write "Atlas is the active focus this week." \
  --subject atlas --kind decision --force-inbox

uv run dory search "active focus"
uv run dory wake --profile coding --budget 1200
```

Serve it over HTTP:

```bash
uv run dory-http --corpus-root data/corpus --index-root .dory/index \
  --host 127.0.0.1 --port 8766
```

Or run it as a durable container:

```bash
cp .env.example .env
mkdir -p data/corpus
docker compose up -d --build
```

Docker binds HTTP to `127.0.0.1:8766` by default. Only set `DORY_HTTP_BIND=0.0.0.0` behind a trusted LAN, VPN, reverse proxy, or firewall.

Compose builds with `network: host` so dependency installs use the host resolver on private DNS setups. If runtime containers cannot resolve external hosts, set `DORY_DOCKER_DNS_SERVERS` in `.env`. Raw `GEMINI_API_KEY` / `OPENROUTER_API_KEY` values are passed through as compatibility aliases for providers that expect those names.

Full walkthrough → [docs/getting-started.md](docs/getting-started.md)

## The loop

```text
wake  →  search  →  get  →  memory-write  →  link
```

- **wake** — bounded hot context at session start
- **search** — hybrid search across durable memory and session evidence
- **get** — exact markdown, with hashes and metadata
- **memory-write** — semantic writes (facts, preferences, decisions, project state)
- **link** — backlinks, neighbors, graph structure

Markdown stays editable by hand. Open it in Obsidian, diff it in git, inspect it in the browser wiki, or let agents update it through guarded write APIs. You always have a human-readable audit trail.

## What's in the box

| Surface | What it does |
|---|---|
| **CLI** | `uv run dory` — init, search, memory-write, research, ops jobs, migrations |
| **HTTP daemon** | `/v1/wake`, `/v1/active-memory`, `/v1/search`, `/v1/research`, `/v1/get`, `/v1/write`, `/v1/memory-write`, `/v1/purge`, `/v1/session-ingest`, `/v1/link`, `/v1/status`, `/v1/stream`, `/metrics`, `/wiki` |
| **Native MCP** | `uv run dory-mcp --mode stdio` or `--mode tcp` |
| **MCP bridge** | HTTP-backed bridge for remote daemons |
| **Hermes provider** | `plugins/hermes-dory/` |
| **OpenClaw package** | `packages/openclaw-dory/` |
| **Browser wiki** | Read/edit the corpus from a browser (auth-gated) |

## Deployment shapes

- **Repo-local** — development, experiments, throwaway corpora.
- **Same-host daemon** — one workstation, all local agents hit `127.0.0.1`.
- **Docker service** — durable always-on daemon, bind-mounted markdown corpus.
- **Private remote host** — LAN box, VPN host, or VPS reachable over HTTP by multiple machines.

The corpus, index, auth tokens, public URL, and model provider keys are environment-specific. Keep them out of the public repo.

## Stack

- **Language** — Python (uv + pyproject)
- **Storage** — Markdown source of truth · SQLite (FTS5, graph edges, embedding cache, chunk vectors, session evidence)
- **Embeddings** — Gemini by default, or an OpenAI-compatible local/LAN embedding endpoint with `DORY_EMBEDDING_PROVIDER=local`
- **Dreaming & maintenance LLM** — Gemini 3.1 Flash via OpenRouter
- **Active-memory LLM** — optional · OpenRouter or any OpenAI-compatible local/LAN endpoint (Ollama, LM Studio, vLLM). The runtime default is OpenRouter when configured; `.env.example` sets it to `off` for deterministic retrieval-only installs
- **Auth** — bearer tokens via `.dory/auth-tokens.json`; `DORY_ALLOW_NO_AUTH=true` for local dev only

## Design influences

Dory is a composite of patterns that already worked:

- **[Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)** — a persistent markdown layer that compounds instead of forcing rediscovery. Dory keeps that, but generates the wiki from a structured memory core.
- **[gbrain](https://github.com/garrytan/gbrain)** — human-readable canonical pages, source-backed evidence, entity resolution, backlinks, read-before-write discipline.
- **[Mem0](https://github.com/mem0ai/mem0)** — scoped memory APIs, explicit add/update/delete semantics, memory ops as first-class tools instead of hidden chat history.
- **[MemPalace](https://github.com/MemPalace/mempalace) / memory palace systems** — bounded wake-up context, local-first session storage, transcript mining, layered recall.
- **Markdown + git** — plain files, diffs, reviews, backups, human inspection.

The goal is practical: one memory layer for all agents, with enough structure to stay useful and enough plain text to stay debuggable.

## Status

- **Core** — CLI, HTTP, MCP, search, and semantic writes are in-repo and covered by tests.
- **Default runtime** — local-first. Server and corpus live wherever you check out.
- **Corpus** — a fresh checkout ships without `core/user.md`, `core/soul.md`, `core/env.md`, `core/active.md`, or the `wiki/` tree. You populate those.
- **Locked goals** — (1) frozen wake-up block, (2) cross-agent shared memory.
- **Public tree** — current implementation, synthetic evals, and integration surfaces. Private planning notes stay private.

## Docs

| | |
|---|---|
| [Getting started](docs/getting-started.md) | Install, init, first wake |
| [Agent integration](docs/agent-integration.md) | Wire up Claude, Codex, opencode, OpenClaw, Hermes |
| [Contributing](CONTRIBUTING.md) | Development setup, validation, commit rules, PR rules |
| [Agent guide](AGENTS.md) | Shared instructions for coding agents working in this repo |
| [Codebase map](docs/current-state/README.md) | Where everything lives |
| [Runtime & data flow](docs/current-state/runtime-and-data-flow.md) | How requests move through the system |
| [Surfaces & integrations](docs/current-state/surfaces-and-integrations.md) | CLI, HTTP, MCP, providers |
| [Operations & validation](docs/current-state/operations-and-validation.md) | Dream, maintain, reindex, migrate |
| [Ops runbook](references/runbook.md) | Day-to-day operation |
| [Client runbook](references/client-runbook.md) | For agent integrators |
| [Evals](eval/README.md) | Benchmarks and coverage |

## Contributing

Contributions are welcome, but the public repo has a hard privacy boundary. Use synthetic data in docs, tests, evals, examples, and fixtures. Do not commit private corpora, raw session logs, real personal memories, direct contact details, local absolute paths, private hostnames, tokens, or `.env` files.

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR. The short version: use Conventional Commits, keep changes scoped, run the relevant `uv` checks, and run `scripts/release/check-public-safety.py` for public docs or artifacts.

## Useful entrypoints

<details>
<summary><b>CLI & search</b></summary>

```bash
uv run dory                                # root command
uv run dory init                           # new corpus
uv run dory search "query"                 # hybrid search
uv run dory memory-write "..." --subject x --kind decision
uv run dory research "What are we working on?" --kind report
```

Set `DORY_GEMINI_API_KEY` or `GOOGLE_API_KEY` before starting HTTP/MCP or any command that embeds, searches, writes semantic memory, reindexes, or runs evals with the default Gemini provider. To use an OpenAI-compatible local/LAN embedding endpoint instead, set `DORY_EMBEDDING_PROVIDER=local` with `DORY_LOCAL_EMBEDDING_*`. Local query embeddings use `DORY_LOCAL_EMBEDDING_QUERY_INSTRUCTION` for Qwen-style retrieval prompts; set it blank to disable. LLM query planning, expansion, and reranking are opt-in via `DORY_QUERY_PLANNER_ENABLED`, `DORY_QUERY_EXPANSION_ENABLED`, `DORY_QUERY_RERANKER_ENABLED`; local reranking uses `DORY_QUERY_RERANKER_PROVIDER=local` and `DORY_LOCAL_RERANKER_*`. `DORY_QUERY_RERANKER_CANDIDATE_LIMIT` caps how many candidates are sent to the reranker per search. For Docker with a LAN/local OpenAI-compatible embedding and rerank server, run Compose with `-f docker-compose.yml -f docker-compose.local.yml`.

</details>

<details>
<summary><b>HTTP & MCP</b></summary>

```bash
uv run dory-http --corpus-root <corpus> --index-root <index>
uv run dory-mcp --mode stdio
uv run dory-mcp --mode tcp --host 127.0.0.1 --port 8765
```

Example MCP config: [`scripts/claude-code/mcp.example.json`](scripts/claude-code/mcp.example.json). HTTP bearer tokens: `uv run dory auth new <name>`. The browser wiki login also needs `DORY_WEB_PASSWORD`.

</details>

<details>
<summary><b>Ops jobs</b></summary>

```bash
uv run dory ops dream-once        # batch dream pass (+ recall-promotion distillation)
uv run dory ops daily-digest-once # summarize shipped sessions into digests/daily/
uv run dory ops maintain-once     # maintenance pass
uv run dory ops wiki-refresh-once # rebuild compiled wiki
uv run dory ops eval-once         # eval batch
uv run dory ops watch             # foreground corpus watcher
```

Installers: `scripts/ops/install-dory.sh`, `install-backup-cron.sh`, `install-ops-launchd.sh`.

</details>

<details>
<summary><b>Legacy corpus migration</b></summary>

```bash
uv run dory --corpus-root <corpus> migrate <legacy-corpus>
uv run dory --corpus-root <corpus> migrate --estimate --sample 25 <legacy-corpus>
uv run dory --corpus-root <corpus> migrate --interactive <legacy-corpus>
```

Stages docs, normalizes to markdown evidence, classifies, extracts memory atoms, bootstraps canonical pages, writes a migration report, quarantines edge cases. Afterwards run `ops wiki-refresh-once`.

</details>

<details>
<summary><b>Session ingestion</b></summary>

Session evidence is stored separately from durable memory. `client` and `solo` installs auto-discover local sessions via `scripts/ops/client-session-shipper.py`. The shipper keeps a local spool plus checkpoint state and polls known harness stores — no manual `--source`. Endpoint: `POST /v1/session-ingest`. `search` with `mode="recall"` reads the session evidence plane directly.

</details>

<details>
<summary><b>Semantic writes</b></summary>

Preferred write surface is semantic, not path-first.

- CLI: `uv run dory memory-write "Atlas prefers concise status notes." --subject atlas --kind preference`
- HTTP: `POST /v1/memory-write`
- MCP: `dory_memory_write`
- OpenClaw: `memory_write`
- Hermes: `memory_write(...)`

Path-first `write` stays available for compatibility and debug flows.

</details>

## License

MIT — see [LICENSE](LICENSE).

## A note on the name

Not affiliated with, endorsed by, or connected to Disney or Pixar. "Dory" is an affectionate nod to the fish who couldn't hold a thought — a fitting mascot for a memory daemon. The GIF is embedded from Giphy as fan reference under fair use. If any rights holder objects, open an issue and it's gone.

---

<div align="center">

*Just keep swimming. 🐟*

</div>
