from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from dory_core.errors import DoryValidationError
from dory_core.index.reindex import reindex_corpus
from dory_core.purge import PurgeEngine
from dory_core.search import SearchEngine
from dory_core.types import PurgeReq, SearchReq


def test_purge_dry_run_reports_paths_without_deleting(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    target = corpus_root / "inbox" / "probe.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\ntitle: Probe\ntype: capture\n---\n\nProbe body.\n", encoding="utf-8")

    response = PurgeEngine(root=corpus_root).purge(PurgeReq(target="inbox/probe.md"))

    assert response.action == "would_purge"
    assert response.dry_run is True
    assert response.paths == ["inbox/probe.md"]
    assert target.exists()


def test_live_purge_requires_hash_and_reason(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    target = corpus_root / "inbox" / "probe.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\ntitle: Probe\ntype: capture\n---\n\nProbe body.\n", encoding="utf-8")

    engine = PurgeEngine(root=corpus_root)

    with pytest.raises(DoryValidationError, match="reason"):
        engine.purge(PurgeReq(target="inbox/probe.md", dry_run=False))

    with pytest.raises(DoryValidationError, match="expected_hash"):
        engine.purge(PurgeReq(target="inbox/probe.md", dry_run=False, reason="cleanup"))


def test_live_purge_deletes_file_and_reindexes(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    target = corpus_root / "inbox" / "probe.md"
    target.parent.mkdir(parents=True)
    text = "---\ntitle: Probe\ntype: capture\n---\n\nunique purge marker xyz.\n"
    target.write_text(text, encoding="utf-8")
    reindex_corpus(corpus_root, index_root, fake_embedder)

    before = SearchEngine(index_root, fake_embedder).search(SearchReq(query="unique purge marker xyz", mode="exact"))
    response = PurgeEngine(root=corpus_root, index_root=index_root, embedder=fake_embedder).purge(
        PurgeReq(
            target="inbox/probe.md",
            dry_run=False,
            expected_hash=f"sha256:{sha256(text.encode('utf-8')).hexdigest()}",
            reason="remove eval artifact",
        )
    )
    after = SearchEngine(index_root, fake_embedder).search(SearchReq(query="unique purge marker xyz", mode="exact"))

    assert before.count == 1
    assert response.action == "purged"
    assert response.indexed is True
    assert not target.exists()
    assert after.count == 0


def test_purge_rejects_canonical_paths_without_explicit_override(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    target = corpus_root / "core" / "user.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\ntitle: User\ntype: core\ncanonical: true\n---\n\nIdentity.\n", encoding="utf-8")

    with pytest.raises(DoryValidationError, match="allow_canonical"):
        PurgeEngine(root=corpus_root).purge(PurgeReq(target="core/user.md"))


def test_purge_can_include_related_tombstone(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    target = corpus_root / "inbox" / "probe.md"
    tombstone = corpus_root / "inbox" / "probe.tombstone.md"
    target.parent.mkdir(parents=True)
    text = "---\ntitle: Probe\ntype: capture\n---\n\nProbe body.\n"
    target.write_text(text, encoding="utf-8")
    tombstone.write_text("---\ntitle: Probe tombstone\ntype: capture\n---\n\nRetired.\n", encoding="utf-8")

    response = PurgeEngine(root=corpus_root).purge(
        PurgeReq(
            target="inbox/probe.md",
            dry_run=False,
            expected_hash=f"sha256:{sha256(text.encode('utf-8')).hexdigest()}",
            reason="remove eval artifact",
            include_related_tombstone=True,
        )
    )

    assert response.paths == ["inbox/probe.md", "inbox/probe.tombstone.md"]
    assert not target.exists()
    assert not tombstone.exists()
