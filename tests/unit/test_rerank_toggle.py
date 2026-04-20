from __future__ import annotations

from dory_core.rerank import resolve_rerank_mode


def test_rerank_auto_is_enabled_by_default() -> None:
    assert resolve_rerank_mode("auto").enabled is True
    assert resolve_rerank_mode("true").enabled is True
    assert resolve_rerank_mode("false").enabled is False


def test_rerank_stays_disabled_in_v0() -> None:
    decision = resolve_rerank_mode("auto", phase="v0")

    assert decision.enabled is False
    assert decision.reason == "rerank disabled in v0"


def test_rerank_false_always_disables() -> None:
    assert resolve_rerank_mode("false", phase="v1").enabled is False
    assert resolve_rerank_mode("false", phase="v0").enabled is False
