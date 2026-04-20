from __future__ import annotations

from dory_core.types import WakeReq
from dory_core.wake import WakeBuilder


def test_wake_builder_returns_frozen_block(sample_corpus_root) -> None:
    resp = WakeBuilder(sample_corpus_root).build(WakeReq(agent="claude-code", budget_tokens=120))

    assert resp.tokens_estimated > 0
    assert resp.block.startswith("---")
    assert resp.frozen_at.tzinfo is not None
