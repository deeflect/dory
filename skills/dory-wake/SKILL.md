---
name: dory-wake
description: Use Dory wake once at the start of a new agent session, after context compaction, or at a real task switch to load bounded hot context with the right profile before substantive work.
---

# Dory Wake

Dory wake is the default session-start memory read for all projects, not only
the Dory repo. Use `dory wake` once at the start of a new agent session, after
context compaction, or at a real task switch before doing substantive work.
Do not rerun wake just because a new user turn arrived when the current wake
block is still in context.

Workflow:
1. Choose the wake profile from the task: `coding` for software/project implementation, `writing` for content/copy/voice work, and `privacy` for boundary-sensitive questions.
2. If a suitable wake block is already present in the current context and no compaction/task switch happened, reuse it instead of calling wake again.
3. Run `dory_wake(profile="<chosen-profile>", budget_tokens=1200)` when MCP tools are available.
4. If the current task or repo maps to a known Dory project, pass `project="<name-or-slug>"`.
5. If MCP is unavailable but the local CLI is available, run `uv run dory --corpus-root <corpus> --index-root <index> wake --agent <agent> --profile <chosen-profile>`.
6. Treat the returned block as frozen startup context, not as editable memory.
7. If the wake block looks stale, use targeted search/get before rerunning wake.

Rules:
- Use `profile=coding` for project/agent work, `profile=writing` for voice/content work, and `profile=privacy` for boundary-sensitive questions
- Wake responses include the selected profile; verify it when debugging agent setup
- Do not call wake repeatedly inside one uncompacted conversation; it bloats context and repeats stale summaries
- Never write directly to `core/` just because wake surfaced something important
- Wake is a hot-start summary, not a substitute for search on factual questions
