# Eval suite

Two tracks:

- **Public synthetic** — `eval/public/questions/` against `examples/corpus/`. Safe to ship.
- **Private canonical** — a local question directory against a local private corpus. Never shipped.

The public suite proves the harness, validator, and doc workflow without exposing a real corpus. The private suite catches the failures that actually matter.

## Layout

- `public/questions/` — public synthetic questions; no private corpus dependencies
- Private question directories — canonical questions grounded in a real corpus; keep out of public releases unless fully scrubbed
- `runs/` — generated eval output
- `categories.md` — one-line description of each category
- `../examples/corpus/` — synthetic markdown corpus used by the public validator

## Question shape

The runner preserves these fields from each YAML:

- `id`
- `question`
- `expected_sources`
- `expected_keywords`
- `type`
- `freshness_sensitive`
- `task_grounded`
- `difficulty`
- `notes`

Output lands in a timestamped run directory:

- `results.json`
- `summary.md`

Runner coverage lives in:

- `tests/integration/cli/test_eval_runner.py`
- `tests/integration/acceptance/test_phase2_shared_memory.py`

## Commands

One public question:

```bash
uv run dory --corpus-root examples/corpus --index-root /tmp/dory-public-index reindex
uv run dory --corpus-root examples/corpus --index-root /tmp/dory-public-index \
  eval run q01 --questions-root eval/public/questions
```

Full public set:

```bash
uv run dory --corpus-root examples/corpus --index-root /tmp/dory-public-index reindex
uv run dory --corpus-root examples/corpus --index-root /tmp/dory-public-index \
  eval run --questions-root eval/public/questions
```

Override input or output paths:

```bash
uv run dory --corpus-root examples/corpus --index-root /tmp/dory-public-index \
  eval run q01 --questions-root eval/public/questions --runs-root eval/runs
```

Validate the public suite:

```bash
python3 eval/validate.py
```

Validate a private suite:

```bash
python3 eval/validate.py --questions-root /path/to/private/questions --corpus-root /path/to/private/corpus
```

Public docs and release notes should quote private evals only as aggregate pass/partial/fail counts with top-k and run date. Never publish private prompts, expected paths, snippets, or run traces.

## Categories

The public synthetic suite currently has 15 questions. It covers:

- `decision-recall` — find the right decision file for "why did we do X"
- `entity-recall` — pull facts about a specific person/project/tool
- `freshness` — return the current version, not a stale one; q05 guards current Beacon state over a superseded archive note
- `temporal` — time-bounded queries
- `negation` — handle "never" / "not" / absence cleanly; q06 uses source-grounded negation so public runs do not require a live judge
- `task-grounded` — memory has to shape a concrete action (config values, paths, env); q11/q13/q14/q15 cover recovery, source-hit, profile-scope, and proposal-safety actions
- `cross-agent` — answer requires content written by multiple agents or sessions; q07 and q09 use synthetic handoff/session-style digest fixtures
- `hot-block` — answer should be in the frozen wake block (no retrieval needed); q08 guards `core/active.md` wake coverage
- `meta` — questions about the memory system itself; q10/q12 cover storage model and observability warnings

## How to extend

When a recall failure appears in a private corpus, add the exact question to the private suite with:

1. the file that should have answered it
2. the keywords that matter
3. a note on why it failed

Questions are append-only. Don't delete — mark `status: retired` in frontmatter when they stop mattering.
