# Evals

Two tracks: public and private.

## Public track

Lives in `eval/public/questions/`, runs against `examples/corpus/`.

Synthetic by design. The goal is to prove the eval harness, validator, and doc workflow without exposing a real user corpus.

```bash
python3 eval/validate.py
uv run dory eval run --questions-root eval/public/questions --runs-root /tmp/dory-public-eval --list-only
```

## Private track

Lives outside the public release boundary or in explicitly gitignored local paths. May use real prompts, source paths, and run traces.

Only aggregate outcomes belong in public docs:

```text
Internal eval on a private corpus: report pass/partial/fail counts only, with top-k and date.
```

Never publish private question text, expected sources, retrieved snippets, traces, or run directories.

Last scrubbed aggregate from the private track: 34/40 passed, 6/40 partial, 0/40 failed at top-k=5. Treat as historical unless a newer private summary exists.
