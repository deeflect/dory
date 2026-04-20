from __future__ import annotations

from pathlib import Path

from dory_core.types import WakeReq, WriteReq
from dory_core.wake import WakeBuilder
from dory_core.write import WriteEngine


def test_phase2_non_negotiable_goals(
    tmp_path: Path,
    sample_corpus_root: Path,
) -> None:
    assert shared_memory_round_trip(tmp_path, sample_corpus_root) is True


def shared_memory_round_trip(tmp_path: Path, sample_corpus_root: Path) -> bool:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    writer = WriteEngine(root=corpus_root)
    writer.write(
        WriteReq(
            kind="append",
            target="core/active.md",
            content="Shared note from Claude Code is now visible in the next Codex wake block.",
        )
    )
    wake_resp = WakeBuilder(corpus_root).build(WakeReq(agent="codex"))
    return "Shared note from Claude Code" in wake_resp.block
