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

Negation and temporal questions can use empty `expected_sources` when testing abstention or time-bound behavior.
