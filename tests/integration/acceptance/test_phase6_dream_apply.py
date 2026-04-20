from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app
from dory_core.index.reindex import reindex_corpus
from dory_core.search import SearchEngine
from dory_core.types import SearchReq


def test_phase6_dream_apply(cli_runner, tmp_path: Path, sample_corpus_root: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    reindex_corpus(corpus_root, index_root, fake_embedder)

    proposals_root = corpus_root / "inbox" / "proposed"
    proposals_root.mkdir(parents=True, exist_ok=True)

    (proposals_root / "proposal-apply.json").write_text(
        json.dumps(
            {
                "proposal_id": "proposal-apply",
                "source_distilled_path": "inbox/distilled/proposal-apply.md",
                "backend": "ollama",
                "actions": [
                    {
                        "action": "replace",
                        "kind": "fact",
                        "subject": "alex",
                        "content": "Alex is now tracked as a design reviewer.",
                        "scope": "person",
                        "confidence": "high",
                        "reason": "Grounded in distilled note.",
                        "source": "dream-proposal",
                        "soft": False,
                    },
                    {
                        "action": "forget",
                        "kind": "decision",
                        "subject": "homeserver",
                        "content": "Superseded by a newer host decision.",
                        "scope": "decision",
                        "confidence": "high",
                        "reason": "Superseded by a newer host decision.",
                        "source": "dream-proposal",
                        "soft": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (proposals_root / "proposal-reject.json").write_text(
        json.dumps(
            {
                "proposal_id": "proposal-reject",
                "source_distilled_path": "inbox/distilled/proposal-reject.md",
                "backend": "openrouter",
                "actions": [
                    {
                        "action": "write",
                        "kind": "note",
                        "subject": "active",
                        "content": "should not land",
                        "scope": "core",
                        "confidence": "low",
                        "reason": "Rejected proposal.",
                        "source": "dream-proposal",
                        "soft": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    apply_result = cli_runner.invoke(
        app,
        ["--corpus-root", str(corpus_root), "--index-root", str(index_root), "dream", "apply", "proposal-apply"],
    )
    reject_result = cli_runner.invoke(
        app,
        ["--corpus-root", str(corpus_root), "--index-root", str(index_root), "dream", "reject", "proposal-reject"],
    )

    assert apply_result.exit_code == 0
    assert reject_result.exit_code == 0
    assert "Alex is now tracked as a design reviewer." in (corpus_root / "people" / "alex.md").read_text(encoding="utf-8")
    assert (corpus_root / "decisions" / "2026-04-07-homeserver.tombstone.md").exists()
    assert (corpus_root / "inbox" / "rejected" / "proposal-reject.json").exists()
    assert not (corpus_root / "inbox" / "never-written.md").exists()

    search = SearchEngine(index_root, fake_embedder).search(SearchReq(query="design reviewer"))
    assert any(result.path == "people/alex.md" for result in search.results)
