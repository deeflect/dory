# Contributing To Dory

Thanks for considering a contribution. Dory is a local-first memory daemon for agents. The repository is public; private corpora and real personal memory data do not belong here.

## Ground Rules

- Keep examples synthetic. Do not add real personal memories, direct contact details, private hostnames, local absolute paths, tokens, or private session logs.
- Prefer small, reviewable changes with focused tests.
- Match the existing architecture before adding a new abstraction.
- Document behavior changes in the same PR that introduces them.
- Be explicit about what you verified and what remains unverified.

## Development Setup

```bash
uv sync --frozen --all-groups
mkdir -p data/corpus
export DORY_CORPUS_ROOT="$PWD/data/corpus"
export DORY_INDEX_ROOT="$PWD/.dory/index"
export DORY_AUTH_TOKENS_PATH="$PWD/.dory/auth-tokens.json"
uv run dory init
```

Run the HTTP daemon locally:

```bash
uv run dory-http --corpus-root data/corpus --index-root .dory/index --host 127.0.0.1 --port 8766
```

OpenClaw plugin work needs its package build too:

```bash
cd packages/openclaw-dory
npm install
npm run build
```

## Validation

Use the smallest checks that prove your change. Common commands:

```bash
uv run ruff check .
uv run pytest -q
uv build --wheel --sdist
docker build -t dory:local .
uv run python eval/validate.py
python3 scripts/release/check-public-safety.py
```

For public docs, examples, evals, fixtures, or release artifacts, run the safety scan on the touched paths:

```bash
python3 scripts/release/check-public-safety.py --path README.md --path docs
```

## Testing Expectations

- Bug fixes should include a regression test unless the behavior is already covered or impractical to isolate.
- Search ranking, active-memory privacy, semantic-write safety, HTTP/MCP schemas, and agent plugin contracts should have focused tests.
- Tests must use synthetic data. Do not encode real user identities, private biography, private relationship facts, sensitive life details, financial details, health details, direct contact details, or private corpus snippets.
- Avoid tests that depend on a developer's machine, private Dory service, private corpus, or network-only resources.

## Commit Rules

Use Conventional Commits:

```text
feat: add active-memory profile selector
fix(search): rank canonical state above raw inbox captures
docs: add OpenClaw gateway troubleshooting
test: cover canonical write dry-run warning
```

Allowed common types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `ci`, `build`, `chore`, `security`.

Commit guidelines:

- Use an imperative subject under 72 characters.
- Keep one logical change per commit.
- Do not mix unrelated formatting churn with behavior changes.
- Mention breaking changes in the commit body and PR description.
- Never commit `.env`, `.dory/`, `.index/`, `data/`, private corpora, raw sessions, API keys, bearer tokens, or machine-specific paths.

## Pull Requests

Your PR should include:

- What changed and why.
- How it was tested, with exact commands.
- Any risk, skipped checks, or follow-up work.
- Screenshots or sample output when changing user-facing CLI, HTTP docs, wiki UI, or plugin behavior.

Keep PRs narrow enough to review. If a change spans core search, HTTP/MCP contracts, plugin packages, and docs, call out each affected surface explicitly.

## Documentation Rules

- Public setup docs should be portable: local paths, hostnames, and tokens must be placeholders.
- If a CLI flag, HTTP field, MCP schema, environment variable, plugin config, or runtime default changes, update the docs that mention it.
- Prefer concrete commands over prose when the command is safe to run in a fresh checkout.

## Release Safety

CI runs tests, package builds, Docker build, public eval validation, and public safety scans. The safety scanner catches obvious leaks; it is not a substitute for judgment. Review public docs and fixtures manually before publishing.
