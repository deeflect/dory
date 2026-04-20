# Eval categories

Shared by the public synthetic suite and the private canonical suite. The public set is small by design; the private set exercises the real failure spread.

| Category | What it tests | Why it matters |
|---|---|---|
| `decision-recall` | "Why did we pick X over Y?" — correct decision file surfaced | Memory is pointless if the reasoning goes missing. |
| `entity-recall` | Facts about a specific person, project, or tool | Kills the "I already told the other agent" tax. |
| `freshness` | Returns the latest, not a stale version | Default Obsidian-style stacks fail this constantly. |
| `temporal` | Handles "what did I think about X before `<date>`" | Bi-temporal semantics — tested even when imperfect early. |
| `negation` | "Have I ever decided NOT to do X?" | Hallucinated certainty is a fail. |
| `task-grounded` | Returns values you can plug into a real command | Memory has to shape actions, not just generate text. |
| `cross-agent` | Answer requires content written by multiple agents or sessions | Cross-agent continuity is the non-negotiable goal. |
| `hot-block` | Answer should be in `core/*` without any search | Validates that wake actually carries the right info. |
| `meta` | Questions about the memory system itself | Self-consistency check for Dory. |
