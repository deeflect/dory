# Hermes Research Publish

Current runbook for publishing Hermes research captures into Dory durable memory.

## Purpose

`dory_publish_research` is a Hermes convenience wrapper for exact-path Dory writes. It is meant for reviewed research notes, benchmark reports, and other durable findings that should land in the corpus as Markdown and be indexed by Dory.

Use it when Hermes has already produced a complete Markdown body and the target should become searchable durable knowledge. Do not use it for raw transcripts, private session logs, secrets, credentials, or unreviewed personal data.

## Target Path

The provider writes research captures under:

```text
knowledge/research/<timestamp>-<slug>.md
```

The slug comes from the title. The timestamp prevents accidental collisions between separate research notes with similar names.

## Frontmatter

Hermes supplies frontmatter for the generated Markdown file:

```yaml
title: <research title>
type: knowledge
source_kind: research
visibility: internal
sensitivity: none
tags:
  - research
  - <optional caller tags>
```

The public repository examples must stay synthetic. Do not place real user biography, direct contact details, credentials, private finance, health details, or local machine paths in research fixtures or docs.

## Write Flow

1. Call `dory_publish_research` with `dry_run: true`.
2. Review the returned target path, frontmatter, body preview, and `indexed: false`.
3. If the capture is public-safe for the intended corpus and the path is correct, call again with `dry_run: false`.
4. On a live write, the provider sends HTTP `POST /v1/write`; Dory writes the file and incrementally indexes the new path.
5. Verify with `dory_search` using concrete terms from the title and body, or `dory_get` on the returned path.

The equivalent lower-level Dory API is `dory_write(kind="create", target="knowledge/research/<timestamp>-<slug>.md", dry_run=true|false, frontmatter=...)`.

## Failure Modes

- `dry_run: true` never writes or indexes. A follow-up `dory_get` on the preview path should return not found.
- A live write fails if the target already exists. Choose a new title or timestamped path instead of replacing by accident.
- Missing or invalid frontmatter should be fixed before live write.
- HTTP auth failures come from the Dory HTTP layer and should be handled before retrying.
- Private or sensitive content should be moved to an appropriate private corpus path, not published through this public research flow.

## Search Phrases

These phrases should discover this runbook once it is indexed into a Dory corpus:

- Hermes publish research to Dory knowledge
- Hermes research publish flow
- `dory_publish_research`
- `knowledge/research`
- exact write dry-run indexed live write
