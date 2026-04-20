from __future__ import annotations

from pathlib import Path

from dory_core.claim_store import ClaimEvent, ClaimRecord, ClaimStore
from dory_core.claims import Claim, EvidenceRef
from dory_core.compiled_wiki import render_compiled_page, render_compiled_page_from_claim_records
from dory_core.ops import run_compiled_wiki_refresh


def test_render_compiled_page_includes_claims_and_evidence() -> None:
    markdown = render_compiled_page(
        title="Rooster",
        summary="Rooster is the active focus this week.",
        claims=(
            Claim(
                id="rooster-focus",
                statement="Rooster is the active focus this week.",
                status="confirmed",
                confidence="high",
                freshness="fresh",
                sources=(
                    EvidenceRef(
                        path="core/active.md",
                        line="1:4",
                        surface="durable",
                        note="Current state doc",
                    ),
                ),
                last_reviewed="2026-04-13T10:00:00Z",
            ),
        ),
        contradictions=("No contradictions found.",),
        open_questions=("Need to confirm next milestone.",),
        last_refreshed="2026-04-13",
    )

    assert "type: wiki" in markdown
    assert "## Key claims" in markdown
    assert "## Evidence" in markdown
    assert "## Timeline" in markdown
    assert "core/active.md" in markdown
    assert "## Contradictions" in markdown
    assert "## Open questions" in markdown


def test_render_compiled_page_from_claim_records() -> None:
    markdown = render_compiled_page_from_claim_records(
        title="Anna",
        summary="Anna prefers written updates.",
        claim_records=(
            ClaimRecord(
                claim_id="claim-1",
                entity_id="person:anna",
                kind="fact",
                statement="Anna prefers written updates.",
                status="active",
                valid_from="2026-04-14",
                valid_to=None,
                confidence="high",
                evidence_path="people/anna.md",
                created_at="2026-04-14",
                updated_at="2026-04-14",
            ),
        ),
        claim_events=(
            ClaimEvent(
                event_id="event-1",
                claim_id="claim-1",
                entity_id="person:anna",
                event_type="added",
                reason=None,
                evidence_path="sources/semantic/2026/04/15/anna-replace.md",
                created_at="2026-04-15T00:00:00Z",
            ),
        ),
        contradictions=(),
        open_questions=(),
        last_refreshed="2026-04-14",
    )

    assert "Anna prefers written updates." in markdown
    assert "### Added" in markdown
    assert "sources/semantic/2026/04/15/anna-replace.md - Anna prefers written updates." in markdown
    assert "2026-04-15T00:00:00Z: Anna prefers written updates." in markdown
    assert "people/anna.md" not in markdown


def test_render_compiled_page_timeline_and_evidence_include_event_types() -> None:
    markdown = render_compiled_page(
        title="Rooster",
        summary="Rooster is the active focus this week.",
        claims=(
            Claim(
                id="claim-1",
                statement="Rooster is the active focus this week.",
                status="confirmed",
                confidence="high",
                freshness="fresh",
                sources=(
                    EvidenceRef(
                        path="core/active.md",
                        line="1:4",
                        surface="durable",
                        note="Current state doc",
                    ),
                ),
                last_reviewed="2026-04-13T10:00:00Z",
            ),
        ),
        claim_events=(
            ClaimEvent(
                event_id="event-1",
                claim_id="claim-1",
                entity_id="project:rooster",
                event_type="added",
                reason=None,
                evidence_path="sources/semantic/2026/04/15/rooster-write.md",
                created_at="2026-04-15T00:00:00Z",
            ),
            ClaimEvent(
                event_id="event-2",
                claim_id="claim-1",
                entity_id="project:rooster",
                event_type="retired",
                reason="superseded",
                evidence_path="sources/semantic/2026/04/16/rooster-forget.md",
                created_at="2026-04-16T00:00:00Z",
            ),
        ),
        contradictions=(),
        open_questions=(),
        last_refreshed="2026-04-16",
    )

    assert "### Added" in markdown
    assert "### Retired" in markdown
    assert "sources/semantic/2026/04/15/rooster-write.md - Rooster is the active focus this week." in markdown
    assert "2026-04-16T00:00:00Z: Retired: Rooster is the active focus this week. (superseded)" in markdown


def test_run_compiled_wiki_refresh_uses_claim_store_records_and_events(tmp_path: Path) -> None:
    source = tmp_path / "people" / "anna.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
title: Anna
type: person
status: active
canonical: true
source_kind: human
temperature: warm
updated: 2026-04-14
---

Anna source summary.
""",
        encoding="utf-8",
    )

    store = ClaimStore(tmp_path / ".dory" / "claim-store.db")
    store.add_claim(
        entity_id="person:anna",
        kind="preference",
        statement="Anna prefers written updates.",
        evidence_path="sources/semantic/2026/04/14/anna-write.md",
        occurred_at="2026-04-14",
    )
    store.add_claim(
        entity_id="person:anna",
        kind="timezone",
        statement="Anna is based in Berlin.",
        evidence_path="sources/semantic/2026/04/15/anna-berlin.md",
        occurred_at="2026-04-15",
    )
    store.replace_current_claim(
        entity_id="person:anna",
        kind="preference",
        statement="Anna prefers concise written updates.",
        evidence_path="sources/semantic/2026/04/16/anna-concise.md",
        reason="new preference confirmed",
        occurred_at="2026-04-16",
    )

    written = run_compiled_wiki_refresh(tmp_path)

    assert "wiki/people/anna.md" in written
    content = (tmp_path / "wiki" / "people" / "anna.md").read_text(encoding="utf-8")
    assert "Anna prefers concise written updates. [active, high, fresh]" in content
    assert "Anna is based in Berlin. [active, high, fresh]" in content
    assert "Anna source summary. [confirmed, high, fresh]" not in content
    assert "### Added" in content
    assert "### Replaced" in content
    assert (
        "sources/semantic/2026/04/16/anna-concise.md - Anna prefers written updates. - new preference confirmed"
        in content
    )
    assert "2026-04-16T00:00:00Z: Replaced: Anna prefers written updates. (new preference confirmed)" in content


def test_run_compiled_wiki_refresh_prunes_orphaned_generated_pages(tmp_path: Path) -> None:
    source = tmp_path / "people" / "anna.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        """---
title: Anna
type: person
status: active
canonical: true
source_kind: human
temperature: warm
updated: 2026-04-14
---

Anna prefers written updates.
""",
        encoding="utf-8",
    )

    wiki_people = tmp_path / "wiki" / "people"
    wiki_people.mkdir(parents=True, exist_ok=True)
    stale_page = wiki_people / "former-anna.md"
    stale_page.write_text(
        """---
title: Former Anna
type: wiki
status: active
canonical: true
source_kind: generated
temperature: warm
updated: 2026-04-10
---

# Former Anna
""",
        encoding="utf-8",
    )
    preserved_page = wiki_people / "notes.md"
    preserved_page.write_text(
        """---
title: Notes
type: wiki
status: active
canonical: true
source_kind: human
temperature: warm
updated: 2026-04-10
---

# Notes
""",
        encoding="utf-8",
    )

    written = run_compiled_wiki_refresh(tmp_path)

    assert "wiki/people/anna.md" in written
    assert not stale_page.exists()
    assert preserved_page.exists()
    assert (tmp_path / "wiki" / "people" / "anna.md").exists()
