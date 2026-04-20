from __future__ import annotations

from pathlib import Path

from dory_core.index.reindex import reindex_corpus
from dory_core.search import SearchEngine
from dory_core.types import SearchReq


def test_search_prefers_live_daily_digest_for_qmd_query_switch(
    tmp_path: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()

    daily_path = corpus_root / "logs" / "daily" / "2026-02-10-digest.md"
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    daily_path.write_text(
        """---
title: qmd fix
created: 2026-02-10
type: daily
date: 2026-02-10
status: done
canonical: true
source_kind: human
temperature: warm
---

- Switched from qmd query to qmd search.
- BM25 was fast and reliable.
""",
        encoding="utf-8",
    )

    older_path = corpus_root / "logs" / "daily" / "2026-02-15.md"
    older_path.parent.mkdir(parents=True, exist_ok=True)
    older_path.write_text(
        """---
title: old qmd plan
created: 2026-02-15
type: daily
date: 2026-02-15
status: superseded
canonical: false
source_kind: extracted
temperature: cold
---

- qmd query was considered for hybrid search.
- Later discussion revisited search modes.
""",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    engine = SearchEngine(index_root, fake_embedder)

    result = engine.search(
        SearchReq(query="Why did we stop using qmd query and what did we switch to?", mode="hybrid", k=2)
    )

    assert result.results[0].path == "logs/daily/2026-02-10-digest.md"


def test_search_prefers_clawsy_project_notes_for_clawzy_pricing_query(
    tmp_path: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()

    state_path = corpus_root / "projects" / "clawsy" / "state.md"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        """---
title: clawsy
created: 2026-02-04
type: project
status: active
canonical: true
source_kind: human
temperature: warm
---

- Hosted OpenClaw SaaS project.
""",
        encoding="utf-8",
    )

    notes_path = corpus_root / "projects" / "clawsy" / "notes-from-daily-digests.md"
    notes_path.write_text(
        """---
title: clawzy — notes from daily digests
created: 2026-04-07
type: project
status: active
canonical: false
source_kind: extracted
temperature: warm
---

- Clawzy pricing: $19/mo BYOK, $49/mo Standard, $99/mo Pro.
- Infrastructure: Hetzner CX22 VPS per user.
""",
        encoding="utf-8",
    )

    distracting_path = corpus_root / "projects" / "openclaw-saas" / "notes-import" / "hosted-openclaw-saas.md"
    distracting_path.parent.mkdir(parents=True, exist_ok=True)
    distracting_path.write_text(
        """---
title: hosted openclaw saas
created: 2026-02-01
type: project
status: active
canonical: false
source_kind: imported
temperature: cold
---

- Hosted OpenClaw SaaS research.
- VPS options were discussed broadly.
""",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    engine = SearchEngine(index_root, fake_embedder)

    result = engine.search(
        SearchReq(
            query="What's the pricing plan for Clawzy and what VPS is it meant to run on?",
            mode="hybrid",
            k=3,
        )
    )

    assert result.results[0].path == "projects/clawsy/notes-from-daily-digests.md"


def test_search_prefers_temporal_digest_for_soul_cleanup_query(
    tmp_path: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()

    digest_path = corpus_root / "logs" / "daily" / "2026-02-09-digest.md"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text(
        """---
title: Mon (02/09) — Crawstr plugin, Plugin routing
created: 2026-02-09
type: daily
date: 2026-02-09
status: done
canonical: false
source_kind: human
temperature: cold
---

- MD brain files cleaned up: SOUL.md, AGENTS.md, TOOLS.md, USER.md, HEARTBEAT.md all deduplicated and trimmed.
- Commit: 3ec22f8.
""",
        encoding="utf-8",
    )

    soul_path = corpus_root / "core" / "soul.md"
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(
        """---
title: SOUL
created: 2026-03-01
type: core
status: active
canonical: true
source_kind: human
temperature: hot
---

- Current SOUL rules and tone guidance.
""",
        encoding="utf-8",
    )

    guide_path = corpus_root / "knowledge" / "dev" / "tools-config" / "openclaw-best-practices.md"
    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text(
        """---
title: OpenClaw Best Practices
created: 2026-02-14
type: knowledge
status: active
canonical: true
source_kind: imported
temperature: warm
---

- Invest in SOUL.md for better experience.
- Tune USER.md and AGENTS.md carefully.
""",
        encoding="utf-8",
    )

    reindex_corpus(corpus_root, index_root, fake_embedder)
    engine = SearchEngine(index_root, fake_embedder)

    result = engine.search(
        SearchReq(query="When did we clean up SOUL.md and the other brain files?", mode="hybrid", k=5)
    )

    assert result.results[0].path == "logs/daily/2026-02-09-digest.md"
