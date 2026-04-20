from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app
from dory_core.index.reindex import reindex_corpus


def test_dream_apply_and_reject(cli_runner, tmp_path: Path, sample_corpus_root: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    reindex_corpus(corpus_root, index_root, fake_embedder)

    proposals_root = corpus_root / "inbox" / "proposed"
    proposals_root.mkdir(parents=True, exist_ok=True)

    (proposals_root / "proposal-001.json").write_text(
        json.dumps(
            {
                "proposal_id": "proposal-001",
                "source_distilled_path": "inbox/distilled/proposal-001.md",
                "backend": "openrouter",
                "actions": [
                    {
                        "action": "replace",
                        "kind": "state",
                        "subject": "active",
                        "content": "Replaced via dream apply.",
                        "scope": "core",
                        "confidence": "high",
                        "reason": "Grounded in distilled note.",
                        "source": "dream-proposal",
                        "soft": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (proposals_root / "proposal-002.json").write_text(
        json.dumps(
            {
                "proposal_id": "proposal-002",
                "source_distilled_path": "inbox/distilled/proposal-002.md",
                "backend": "openrouter",
                "actions": [
                    {
                        "action": "write",
                        "kind": "note",
                        "subject": "active",
                        "content": "should never apply",
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

    listed = cli_runner.invoke(
        app,
        ["--corpus-root", str(corpus_root), "--index-root", str(index_root), "dream", "list"],
    )
    applied = cli_runner.invoke(
        app,
        ["--corpus-root", str(corpus_root), "--index-root", str(index_root), "dream", "apply", "proposal-001"],
    )
    rejected = cli_runner.invoke(
        app,
        ["--corpus-root", str(corpus_root), "--index-root", str(index_root), "dream", "reject", "proposal-002"],
    )

    assert listed.exit_code == 0
    assert "proposal-001" in listed.stdout
    assert applied.exit_code == 0
    assert "core/active.md" in applied.stdout
    assert "Replaced via dream apply." in (corpus_root / "core" / "active.md").read_text(encoding="utf-8")
    assert rejected.exit_code == 0
    assert not (proposals_root / "proposal-002.json").exists()
    assert (corpus_root / "inbox" / "rejected" / "proposal-002.json").exists()
