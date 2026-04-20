---
name: dory-wake
description: Use Dory wake at session start or task switches to load bounded hot context with the right profile before substantive work.
---

# Dory Wake

Use `dory wake` at session start or task switch before doing substantive work.

Workflow:
1. Run `uv run dory --corpus-root <corpus> --index-root <index> wake --agent <agent> --profile coding`
2. Treat the returned block as frozen startup context, not as editable memory
3. If the wake block looks stale, search before assuming it is wrong

Rules:
- Use `profile=coding` for project/agent work, `profile=writing` for voice/content work, and `profile=privacy` for boundary-sensitive questions
- Wake responses include the selected profile; verify it when debugging agent setup
- Never write directly to `core/` just because wake surfaced something important
- Wake is a hot-start summary, not a substitute for search on factual questions
