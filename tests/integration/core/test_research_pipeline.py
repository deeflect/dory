from __future__ import annotations

from pathlib import Path

from dory_core.index.reindex import reindex_corpus
from dory_core.research import ResearchEngine
from dory_core.search import SearchEngine


def test_research_pipeline_builds_artifact_from_compiled_wiki(
    tmp_path: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    corpus_root.mkdir()

    wiki_path = corpus_root / "wiki" / "projects" / "rooster.md"
    wiki_path.parent.mkdir(parents=True, exist_ok=True)
    wiki_path.write_text(
        """---
title: Rooster
type: wiki
status: active
canonical: true
source_kind: generated
temperature: warm
updated: 2026-04-13
---

# Rooster

## Summary
Rooster is the active focus this week.

## Key claims
- Rooster is the active focus this week. [confirmed, high, fresh]

## Sources
- core/active.md
""",
        encoding="utf-8",
    )

    support_path = corpus_root / "projects" / "rooster" / "state.md"
    support_path.parent.mkdir(parents=True, exist_ok=True)
    support_path.write_text(
        """---
title: Rooster
type: project
status: active
canonical: true
source_kind: human
temperature: warm
---

Rooster remains the active project focus.
""",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    engine = ResearchEngine(search_engine=SearchEngine(index_root, fake_embedder))

    resp = engine.research("What are we working on right now?", corpus="all", kind="report")

    assert resp.artifact.kind == "report"
    assert resp.sources[0] in {
        "wiki/projects/rooster.md",
        "projects/rooster/state.md",
    }
    assert "wiki/projects/rooster.md" in resp.sources
    assert "## Answer" in resp.artifact.body
    assert "## Evidence" in resp.artifact.body
    assert "Rooster" in resp.artifact.body
    assert "wiki/projects/rooster.md" in resp.artifact.sources
