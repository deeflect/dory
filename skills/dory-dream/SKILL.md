# Dory Dream

Use the dreaming flow after a session closes, never during live conversation.

Workflow:
1. Distill the session:
   `uv run dory --corpus-root <corpus> --index-root <index> dream distill logs/sessions/<agent>/<date>.md`
2. Generate reviewable proposals:
   `uv run dory --corpus-root <corpus> --index-root <index> dream propose <distilled-id>`
3. Review proposals, then:
   - `dory dream apply <id>`
   - or `dory dream reject <id>`

Rules:
- Distilled notes and proposals are review artifacts, not automatic memory mutations
- Proposal application routes through semantic writes, so canonical writes still need the normal semantic-write safety expectations
- Use `ops dream-once` for batch processing
- Dreaming requires the configured dream backend; OpenRouter is default, Ollama is used only in sovereign mode
