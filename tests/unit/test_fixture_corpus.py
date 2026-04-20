from __future__ import annotations

from pathlib import Path


def test_fixture_corpus_contains_hot_block_files() -> None:
    root = Path("tests/fixtures/dory_sample/core")

    assert (root / "user.md").exists()
    assert (root / "soul.md").exists()
    assert (root / "env.md").exists()
    assert (root / "active.md").exists()
