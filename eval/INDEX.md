# Eval question index

Two tracks:

- **Public synthetic** — `eval/public/questions/` against `examples/corpus/`
- **Private canonical** — a gitignored local question directory against a local private corpus

The public suite ships with the repo. The private suite is never described question-by-question in public docs — prompts, expected source paths, and run artifacts can leak corpus contents.

## Public synthetic suite

| ID | Category | Purpose |
|---|---|---|
| `q01` | `entity-recall` | Retrieve a synthetic project overview. |
| `q02` | `decision-recall` | Explain why the public suite is synthetic. |
| `q03` | `task-grounded` | Recover the validation command for a private suite. |
| `q04` | `temporal` | Retrieve a dated synthetic eval note. |
| `q05` | `freshness` | Prefer Beacon's current cobalt launch state over a superseded amber archive note. |
| `q06` | `negation` | Recover the explicit decision not to enable remote sync by default. |
| `q07` | `cross-agent` | Recall a Relay handoff with contributions from two demo agents. |
| `q08` | `hot-block` | Check that wake can carry the Beacon release-gate marker from `core/active.md`. |
| `q09` | `cross-agent` | Use a durable synthetic session-style digest for scoped recall behavior. |
| `q10` | `meta` | Explain Dory's markdown source-of-truth and disposable SQLite sidecar model. |
| `q11` | `task-grounded` | Give an agent recovery path for a missing expected eval source. |
| `q12` | `meta` | Surface structured retrieval warning names for planner/rerank/fallback/scope ambiguity. |
| `q13` | `task-grounded` | Recall source-hit counter guidance for retrieval-quality handoffs. |
| `q14` | `task-grounded` | State profile-scoped retrieval paths to prioritize and avoid for coding tasks. |
| `q15` | `task-grounded` | Prefer proposal flow when semantic write target resolution is ambiguous. |

## Private suite policy

Report private evals publicly only as aggregates:

```text
Internal eval on a private corpus: <passed>/<total> passed, <partial>/<total> partial, <failed>/<total> failed at top-k=<k>, run date <date>.
```

Never publish private question text, source paths, run traces, or retrieved snippets.

Historical private aggregate retained for release notes: 34/40 passed, 6/40 partial, 0/40 failed at top-k=5. Refresh from a new private run before quoting it as current.

## Validator

Every question is validated by `eval/validate.py`:

1. YAML parses.
2. `id` matches filename prefix.
3. Required fields are present.
4. Every `expected_sources` path exists under the configured corpus root.
5. Every `expected_keywords` entry appears case-insensitively in at least one expected source file.

Public suite:

```bash
python3 eval/validate.py
```

Private suite:

```bash
python3 eval/validate.py --questions-root /path/to/private/questions --corpus-root /path/to/private/corpus
```

Negation and temporal questions can use empty `expected_sources` when testing abstention or time-bound behavior. The current public suite uses source-grounded negation so it can run without a live judge.
