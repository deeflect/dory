from __future__ import annotations

from pathlib import Path

from dory_core.index.reindex import ReindexResult
from dory_core.search import SearchEngine
from dory_core.types import SearchReq, WriteReq
from dory_core.write import WriteEngine


def test_write_append_creates_file_and_reindexes(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    engine = WriteEngine(root=corpus_root, index_root=index_root, embedder=fake_embedder)
    resp = engine.write(
        WriteReq(
            kind="append",
            target="inbox/note.md",
            content="Shared memory note about palette work.",
            frontmatter={"title": "Inbox note", "type": "capture", "tags": ["palette"]},
        )
    )

    assert resp.action == "appended"
    assert resp.indexed is True
    assert (corpus_root / "inbox/note.md").exists()

    results = SearchEngine(index_root, fake_embedder).search(SearchReq(query="palette"))
    assert any(result.path == "inbox/note.md" for result in results.results)


def test_write_append_merges_frontmatter_tags(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    target = corpus_root / "inbox/note.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\ntitle: Inbox note\ntype: capture\ntags: [alpha]\n---\n\nExisting body.\n",
        encoding="utf-8",
    )

    engine = WriteEngine(root=corpus_root)
    engine.write(
        WriteReq(
            kind="append",
            target="inbox/note.md",
            content="Second line.",
            frontmatter={"tags": ["beta"]},
        )
    )

    written = target.read_text(encoding="utf-8")
    assert "tags:\n- alpha\n- beta" in written
    assert "Second line." in written


def test_write_append_auto_places_project_state(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    engine = WriteEngine(root=corpus_root)

    resp = engine.write(
        WriteReq(
            kind="append",
            target="launch-plan.md",
            content="Canonical project state.",
            frontmatter={"title": "Launch Plan", "type": "project"},
        )
    )

    assert resp.path == "projects/launch-plan/state.md"
    assert (corpus_root / "projects" / "launch-plan" / "state.md").exists()


def test_write_append_keeps_timeline_entries_below_marker(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    target = corpus_root / "projects" / "dory" / "state.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """---
title: Dory
type: project
status: active
created: 2026-04-01
updated: 2026-04-01
---

Current state.

<!-- TIMELINE: append-only below this line -->

- 2026-04-01: Initial project state.
""",
        encoding="utf-8",
    )

    engine = WriteEngine(root=corpus_root)
    engine.write(
        WriteReq(
            kind="append",
            target="projects/dory/state.md",
            content="- 2026-04-10: Added stale warnings.",
            frontmatter={},
        )
    )

    written = target.read_text(encoding="utf-8")
    assert "has_timeline: true" in written
    assert "<!-- TIMELINE: append-only below this line -->" in written
    assert written.index("- 2026-04-10: Added stale warnings.") > written.index(
        "<!-- TIMELINE: append-only below this line -->"
    )


def test_write_append_updates_compiled_truth_above_timeline(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    target = corpus_root / "projects" / "dory" / "state.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """---
title: Dory
type: project
status: active
created: 2026-04-01
updated: 2026-04-01
---

Current state.

<!-- TIMELINE: append-only below this line -->

- 2026-04-01: Initial project state.
""",
        encoding="utf-8",
    )

    engine = WriteEngine(root=corpus_root)
    engine.write(
        WriteReq(
            kind="append",
            target="projects/dory/state.md",
            content="Current state now includes OpenRouter dreaming.",
            frontmatter={},
        )
    )

    written = target.read_text(encoding="utf-8")
    assert written.index("Current state now includes OpenRouter dreaming.") < written.index(
        "<!-- TIMELINE: append-only below this line -->"
    )
    assert "updated:" in written


def test_write_uses_incremental_reindex_path(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
    monkeypatch,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    corpus_root.mkdir()
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    calls: list[list[str]] = []

    def _fake_reindex_paths(root: Path, index: Path, embedder: object, relative_paths: list[str]) -> ReindexResult:
        assert root == corpus_root
        assert index == index_root
        assert embedder is fake_embedder
        calls.append(relative_paths)
        return ReindexResult(files_indexed=1, chunks_indexed=1, vectors_indexed=1)

    monkeypatch.setattr("dory_core.write.reindex_paths", _fake_reindex_paths)
    monkeypatch.setattr("dory_core.write.sync_document_edges", lambda *args, **kwargs: 0)

    engine = WriteEngine(root=corpus_root, index_root=index_root, embedder=fake_embedder)
    response = engine.write(
        WriteReq(
            kind="append",
            target="inbox/note.md",
            content="Incremental index note.",
            frontmatter={"title": "Inbox note", "type": "capture"},
        )
    )

    assert response.indexed is True
    assert calls == [["inbox/note.md"]]
