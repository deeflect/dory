from __future__ import annotations

from pathlib import Path

from dory_core.index.reindex import reindex_corpus
from dory_core.search import SearchEngine
from dory_core.types import SearchReq


def test_search_prefers_compiled_wiki_page_for_sample_state_query(
    tmp_path: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    corpus_root.mkdir()

    wiki_path = corpus_root / "wiki" / "projects" / "sample.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Sample
type: wiki
status: active
canonical: true
source_kind: generated
temperature: warm
updated: 2026-04-13
---

# Sample

## Summary
Sample is the active focus this week.

## Key claims
- Sample is the active focus this week. [confirmed, high, fresh]

## Evidence
- sample-focus
  - core/active.md (1:4) [durable] Current state doc
""",
        encoding="utf-8",
    )

    support_path = corpus_root / "projects" / "sample" / "notes.md"
    support_path.parent.mkdir(parents=True, exist_ok=True)
    support_path.write_text(
        """---
title: sample notes
type: project
status: active
canonical: false
source_kind: extracted
temperature: cold
---

Sample was discussed, but this is just support material.
""",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    engine = SearchEngine(index_root, fake_embedder)

    response = engine.search(SearchReq(query="Sample active focus", mode="hybrid", k=3))

    assert response.results[0].path == "wiki/projects/sample.md"
    assert response.results[0].frontmatter["type"] == "wiki"
