# Dory Maintain

Use Dory maintenance to inspect corpus quality and generate suggested cleanup reports.

Workflow:
1. Inspect one document:
   `uv run dory --corpus-root <corpus> --index-root <index> maintain inspect <path> --write-report`
2. Inspect the default hot set:
   `uv run dory --corpus-root <corpus> --index-root <index> ops maintain-once`
3. Check compiled wiki drift:
   `uv run dory --corpus-root <corpus> --index-root <index> maintain wiki-health --write-report`
4. Review reports under `inbox/maintenance/`

Rules:
- Maintenance emits suggestions only
- Do not bulk-apply maintenance reports without review
- Prefer metadata and placement fixes over rewriting body content
- Wiki health checks compare generated wiki pages against claim-store state and evidence; review mismatches before rewriting canonical pages
