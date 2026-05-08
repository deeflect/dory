## Summary

- 

## Verification

- [ ] `uv run ruff check .`
- [ ] `uv run pytest -q`
- [ ] `(cd packages/openclaw-dory && npm ci && npm run build)`
- [ ] `uv build --wheel --sdist`
- [ ] `uv run python eval/validate.py`
- [ ] `uv run python scripts/release/check-public-safety.py`
- [ ] `uv run python scripts/release/check-public-safety.py --path dist`
- [ ] Other:

## Public-Safety Checklist

- [ ] No real tokens, private hostnames, local absolute paths, private corpus files, or raw session logs.
- [ ] Tests and fixtures use synthetic data only.
- [ ] Docs use portable placeholders and do not mention private deployments.
- [ ] OpenClaw plugin changes include rebuilt `packages/openclaw-dory/dist/index.js` when needed.

## Notes For Reviewers

- Risk:
- Skipped checks:
- Follow-up:
