from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from dory_core.dreaming.events import SessionClosedEvent
from dory_core.dreaming.extract import DistillationWriter, OpenRouterSessionDistiller
from dory_core.ops import run_compiled_wiki_refresh


def test_distillation_writer_persists_summary(tmp_path: Path) -> None:
    writer = DistillationWriter(tmp_path)
    event = SessionClosedEvent(
        agent="codex",
        session_path="logs/sessions/codex/2026-04-07.md",
        closed_at=datetime(2026, 4, 7, 21, 0, tzinfo=UTC),
    )

    target = writer.write(event, "Decided to keep Phase 6 proposal-based.")

    assert target == tmp_path / "inbox/distilled/codex-2026-04-07.md"
    written = target.read_text(encoding="utf-8")
    assert "Source session: logs/sessions/codex/2026-04-07.md" in written
    assert "Decided to keep Phase 6 proposal-based." in written


def test_openrouter_session_distiller_writes_structured_sections(tmp_path: Path) -> None:
    class FakeClient:
        def generate_json(self, **kwargs):
            return {
                "summary": "Session focused on pricing and deployment choices.",
                "key_facts": ["Clawsy pricing settled around BYOK tiers."],
                "decisions": ["Keep proposals reviewable before apply."],
                "followups": ["Reindex after corpus cleanup."],
                "entities": ["Clawsy", "Dory"],
            }

    event = SessionClosedEvent(
        agent="codex",
        session_path="logs/sessions/codex/2026-04-10.md",
        closed_at=datetime(2026, 4, 10, 21, 0, tzinfo=UTC),
    )
    distiller = OpenRouterSessionDistiller(client=FakeClient(), writer=DistillationWriter(tmp_path))  # type: ignore[arg-type]

    target = distiller.distill(event, "Session body")

    written = target.read_text(encoding="utf-8")
    assert "## Summary" in written
    assert "## Decisions" in written
    assert "- Keep proposals reviewable before apply." in written
    assert "- Clawsy" in written


def test_compiled_wiki_refresh_writes_project_page(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    source = corpus_root / "core" / "active.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
title: Rooster
type: core
status: active
canonical: true
source_kind: human
temperature: hot
updated: 2026-04-13
---

Rooster is the active focus this week.
""",
        encoding="utf-8",
    )

    written = run_compiled_wiki_refresh(corpus_root)

    assert "wiki/projects/rooster.md" in written
    assert "wiki/index.md" in written
    assert "wiki/hot.md" in written
    assert "wiki/log.md" in written
    target = corpus_root / "wiki" / "projects" / "rooster.md"
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "type: wiki" in content
    assert "Rooster is the active focus this week." in content
