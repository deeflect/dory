"""Unit tests for the shared hot-context packet module.

Tests focus on:
- ``SourceBackedItem`` construction and immutability.
- ``HotContextPacket`` construction, immutability, and field access.
- ``source_backed_items_from_results`` conversion from result-like objects.
- ``dedupe_sources`` merge/dedup behaviour.
- ``render_packet_to_block`` output shape.
- ``render_packet_summary`` output shape.
"""

from __future__ import annotations

from dory_core.entity_context import EntityContext
from dory_core.hot_context import (
    HotContextPacket,
    SourceBackedItem,
    dedupe_sources,
    render_packet_to_block,
    render_packet_summary,
    source_backed_items_from_results,
)


def _make_stub_result(*, path: str, snippet: str, score: float = 0.5) -> object:
    return type(
        "StubResult",
        (),
        {
            "path": path,
            "lines": "1:1",
            "score": score,
            "snippet": snippet,
            "frontmatter": {},
            "stale_warning": None,
            "confidence": "high",
        },
    )()


# ---------------------------------------------------------------------------
# SourceBackedItem
# ---------------------------------------------------------------------------


class TestSourceBackedItem:
    def test_constructs_with_text_only(self) -> None:
        item = SourceBackedItem(text="hello")
        assert item.text == "hello"
        assert item.source_path is None

    def test_constructs_with_text_and_source(self) -> None:
        item = SourceBackedItem(text="hello", source_path="core/test.md")
        assert item.text == "hello"
        assert item.source_path == "core/test.md"

    def test_is_frozen_and_slotted(self) -> None:
        item = SourceBackedItem(text="a")
        import dataclasses
        assert dataclasses.is_dataclass(item)
        assert hasattr(item, "__slots__")

    def test_is_frozen_prevents_mutation(self) -> None:
        item = SourceBackedItem(text="hello")
        import pytest
        with pytest.raises(AttributeError):
            item.text = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# HotContextPacket
# ---------------------------------------------------------------------------


class TestHotContextPacket:
    MINIMAL_PKT = HotContextPacket(
        profile="default",
        guardrails=(),
        project=None,
        entity_context=(),
        active_claims=(),
        observations=(),
        durable_evidence=(),
        session_evidence=(),
        sources=(),
        warnings=(),
        partial=False,
    )

    def test_minimal_constructs(self) -> None:
        pkt = self.MINIMAL_PKT
        assert pkt.profile == "default"
        assert pkt.partial is False

    def test_full_constructs(self) -> None:
        ec = EntityContext(
            entity_id="project:test",
            canonical_name="Test",
            family="project",
            canonical_path="projects/test/state.md",
            matched_by="project_handle",
            source_refs=("projects/test/state.md",),
        )
        pkt = HotContextPacket(
            profile="coding",
            guardrails=("no personal data",),
            project=ec,
            entity_context=(ec,),
            active_claims=(SourceBackedItem(text="Active work", source_path="core/active.md"),),
            observations=(SourceBackedItem(text="Observation", source_path="core/obs.md"),),
            durable_evidence=(SourceBackedItem(text="Durable evidence", source_path="projects/test/state.md"),),
            session_evidence=(SourceBackedItem(text="Session note", source_path="logs/sessions/s1.md"),),
            sources=("core/active.md", "projects/test/state.md"),
            warnings=("partial data",),
            partial=False,
        )
        assert pkt.profile == "coding"
        assert pkt.project is not None
        assert pkt.project.canonical_name == "Test"
        assert len(pkt.active_claims) == 1
        assert len(pkt.entity_context) == 1
        assert len(pkt.sources) == 2
        assert len(pkt.warnings) == 1

    def test_is_frozen_and_slotted(self) -> None:
        pkt = self.MINIMAL_PKT
        import dataclasses
        assert dataclasses.is_dataclass(pkt)
        assert hasattr(pkt, "__slots__")


# ---------------------------------------------------------------------------
# source_backed_items_from_results
# ---------------------------------------------------------------------------


class TestSourceBackedItemsFromResults:
    def test_empty_results(self) -> None:
        items = source_backed_items_from_results([])
        assert items == ()

    def test_results_with_duplicates(self) -> None:
        r1 = _make_stub_result(path="core/active.md", snippet="Active work.")
        r2 = _make_stub_result(path="core/active.md", snippet="Active work.")
        items = source_backed_items_from_results([r1, r2], max_items=10)
        # Results are converted individually; dedup is caller's responsibility
        assert len(items) == 2

    def test_respects_max_items(self) -> None:
        results = [_make_stub_result(path=f"path{i}.md", snippet=f"Snippet {i}") for i in range(10)]
        items = source_backed_items_from_results(results, max_items=3)
        assert len(items) == 3

    def test_truncates_snippet(self) -> None:
        long_text = "A" * 500
        r = _make_stub_result(path="test.md", snippet=long_text)
        items = source_backed_items_from_results([r], snippet_chars=50)
        assert len(items) == 1
        assert len(items[0].text) <= 55  # 50 + ellipsis wiggle room

    def test_skips_results_without_path(self) -> None:
        r = _make_stub_result(path="", snippet="no path")
        items = source_backed_items_from_results([r])
        assert items == ()

    def test_skips_results_without_snippet(self) -> None:
        r = _make_stub_result(path="test.md", snippet="")
        items = source_backed_items_from_results([r])
        assert items == ()


# ---------------------------------------------------------------------------
# dedupe_sources
# ---------------------------------------------------------------------------


class TestDedupeSources:
    def test_empty(self) -> None:
        assert dedupe_sources() == ()

    def test_single_source(self) -> None:
        assert dedupe_sources(["a.md", "b.md"]) == ("a.md", "b.md")

    def test_merges_and_dedupes_preserving_order(self) -> None:
        assert dedupe_sources(["a.md"], ["b.md"], ["a.md"]) == ("a.md", "b.md")

    def test_handles_empty_lists(self) -> None:
        assert dedupe_sources([], ["a.md"], []) == ("a.md",)

    def test_strips_whitespace(self) -> None:
        assert dedupe_sources(["  a.md  ", "b.md "]) == ("a.md", "b.md")


# ---------------------------------------------------------------------------
# render_packet_to_block
# ---------------------------------------------------------------------------


class TestRenderPacketToBlock:
    def empty_packet(self) -> HotContextPacket:
        return HotContextPacket(
            profile="default",
            guardrails=(),
            project=None,
            entity_context=(),
            active_claims=(),
            observations=(),
            durable_evidence=(),
            session_evidence=(),
            sources=(),
            warnings=(),
            partial=False,
        )

    def test_empty_packet_renders_empty_block(self) -> None:
        block = render_packet_to_block(self.empty_packet(), budget_tokens=400)
        assert block == ""

    def test_active_claims_render_as_active_memory_section(self) -> None:
        pkt = HotContextPacket(
            profile="test",
            guardrails=(),
            project=None,
            entity_context=(),
            active_claims=(SourceBackedItem(text="First claim"), SourceBackedItem(text="Second claim")),
            observations=(),
            durable_evidence=(),
            session_evidence=(),
            sources=(),
            warnings=(),
            partial=False,
        )
        block = render_packet_to_block(pkt, budget_tokens=400)
        assert "## Active memory" in block
        assert "- First claim" in block
        assert "- Second claim" in block

    def test_observations_included_in_active_memory_section(self) -> None:
        pkt = HotContextPacket(
            profile="test",
            guardrails=(),
            project=None,
            entity_context=(),
            active_claims=(),
            observations=(SourceBackedItem(text="Observation", source_path="projects/dory/state.md"),),
            durable_evidence=(),
            session_evidence=(),
            sources=(),
            warnings=(),
            partial=False,
        )
        block = render_packet_to_block(pkt, budget_tokens=400)
        assert "## Active memory" in block
        assert "- Observation (source: projects/dory/state.md)" in block

    def test_durable_evidence_section(self) -> None:
        pkt = HotContextPacket(
            profile="test",
            guardrails=(),
            project=None,
            entity_context=(),
            active_claims=(),
            observations=(),
            durable_evidence=(SourceBackedItem(text="Durable snippet", source_path="core/test.md"),),
            session_evidence=(),
            sources=(),
            warnings=(),
            partial=False,
        )
        block = render_packet_to_block(pkt, budget_tokens=400)
        assert "## Durable evidence" in block
        assert "core/test.md" in block
        assert "Durable snippet" in block

    def test_wake_context_renders_between_active_and_evidence(self) -> None:
        pkt = HotContextPacket(
            profile="test",
            guardrails=(),
            project=None,
            entity_context=(),
            active_claims=(SourceBackedItem(text="Active claim"),),
            observations=(),
            durable_evidence=(SourceBackedItem(text="Durable snippet", source_path="core/test.md"),),
            session_evidence=(),
            sources=(),
            warnings=(),
            partial=False,
            wake_context=(SourceBackedItem(text="# Wake\n\nWake context."),),
        )

        block = render_packet_to_block(pkt, budget_tokens=400)

        assert block.index("## Active memory") < block.index("# Wake")
        assert block.index("# Wake") < block.index("## Durable evidence")

    def test_session_evidence_section(self) -> None:
        pkt = HotContextPacket(
            profile="test",
            guardrails=(),
            project=None,
            entity_context=(),
            active_claims=(),
            observations=(),
            durable_evidence=(),
            session_evidence=(SourceBackedItem(text="Session snippet", source_path="logs/sessions/s1.md"),),
            sources=(),
            warnings=(),
            partial=False,
        )
        block = render_packet_to_block(pkt, budget_tokens=400)
        assert "## Session evidence" in block
        assert "logs/sessions/s1.md" in block
        assert "Session snippet" in block

    def test_respects_budget(self) -> None:
        long_text = "Long text. " * 500
        pkt = HotContextPacket(
            profile="test",
            guardrails=(),
            project=None,
            entity_context=(),
            active_claims=(SourceBackedItem(text=long_text),),
            observations=(),
            durable_evidence=(),
            session_evidence=(),
            sources=(),
            warnings=(),
            partial=False,
        )
        block = render_packet_to_block(pkt, budget_tokens=10)
        # Should be truncated to fit budget
        assert len(block) < len(long_text)


# ---------------------------------------------------------------------------
# render_packet_summary
# ---------------------------------------------------------------------------


class TestRenderPacketSummary:
    def test_empty_packet_returns_empty(self) -> None:
        pkt = HotContextPacket(
            profile="default",
            guardrails=(),
            project=None,
            entity_context=(),
            active_claims=(),
            observations=(),
            durable_evidence=(),
            session_evidence=(),
            sources=(),
            warnings=(),
            partial=False,
        )
        assert render_packet_summary(pkt) == ""

    def test_uses_first_active_claim(self) -> None:
        pkt = HotContextPacket(
            profile="test",
            guardrails=(),
            project=None,
            entity_context=(),
            active_claims=(SourceBackedItem(text="First claim"), SourceBackedItem(text="Second claim")),
            observations=(),
            durable_evidence=(),
            session_evidence=(),
            sources=(),
            warnings=(),
            partial=False,
        )
        summary = render_packet_summary(pkt)
        assert "First claim" in summary

    def test_truncates_to_280_chars(self) -> None:
        long_text = "A" * 300
        pkt = HotContextPacket(
            profile="test",
            guardrails=(),
            project=None,
            entity_context=(),
            active_claims=(SourceBackedItem(text=long_text),),
            observations=(),
            durable_evidence=(),
            session_evidence=(),
            sources=(),
            warnings=(),
            partial=False,
        )
        summary = render_packet_summary(pkt)
        assert len(summary) <= 280
