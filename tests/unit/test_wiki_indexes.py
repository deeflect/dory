from __future__ import annotations

from pathlib import Path

from dory_core.claim_store import ClaimStore
from dory_core.maintenance import MemoryHealthDashboard
from dory_core.wiki_indexes import WikiIndexBuilder


def test_wiki_index_builder_writes_global_and_family_indexes(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki"
    (wiki_root / "projects").mkdir(parents=True)
    (wiki_root / "projects" / "rooster.md").write_text(
        "---\ntitle: Rooster\ntype: wiki\nstatus: active\n---\n\nRooster is the active focus.\n",
        encoding="utf-8",
    )
    (wiki_root / "people").mkdir(parents=True)
    (wiki_root / "people" / "anna.md").write_text(
        "---\ntitle: Anna\ntype: wiki\nstatus: active\n---\n\nAnna is a person.\n",
        encoding="utf-8",
    )

    written = WikiIndexBuilder(tmp_path).refresh()

    assert "wiki/index.md" in written
    assert "wiki/hot.md" in written
    assert "wiki/log.md" in written
    assert "wiki/projects/index.md" in written
    assert "wiki/people/index.md" in written
    assert (tmp_path / "wiki" / "index.md").exists()
    global_index = (tmp_path / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "[[wiki/projects/index|Projects]]" in global_index
    assert "[[wiki/people/index|People]]" in global_index
    assert "[[wiki/hot|Hot Cache]]" in global_index
    assert "[[wiki/log|Activity Log]]" in global_index

    projects_index = (tmp_path / "wiki" / "projects" / "index.md").read_text(encoding="utf-8")
    assert "[[wiki/projects/rooster|Rooster]]" in projects_index

    hot_page = (tmp_path / "wiki" / "hot.md").read_text(encoding="utf-8")
    assert "## Last Updated" in hot_page
    assert "## Key Recent Facts" in hot_page
    assert "## Current Focus" in hot_page
    assert "## Recent Pages" in hot_page

    log_page = (tmp_path / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "## Recent Wiki Changes" in log_page


def test_wiki_hot_page_prefers_claim_event_facts_when_claim_store_exists(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / ".dory" / "claim-store.db")
    store.add_claim(
        entity_id="project:rooster",
        kind="state",
        statement="Rooster is the active focus this week.",
        evidence_path="sources/semantic/2026/04/14/rooster-write.md",
    )
    wiki_root = tmp_path / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "rooster.md").write_text(
        "---\n"
        "title: Rooster\n"
        "type: wiki\n"
        "status: active\n"
        "updated: 2026-04-14\n"
        "---\n\n"
        "# Rooster\n\n"
        "Rooster compiled page.\n",
        encoding="utf-8",
    )

    WikiIndexBuilder(tmp_path).refresh()

    hot_page = (tmp_path / "wiki" / "hot.md").read_text(encoding="utf-8")
    assert "## Key Recent Facts" in hot_page
    assert "- Rooster is the active focus this week." in hot_page
    assert "## Recent Changes" in hot_page
    assert "added Rooster is the active focus this week." in hot_page

    log_page = (tmp_path / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "## Recent Claim Changes" in log_page
    assert "added Rooster is the active focus this week." in log_page


def test_wiki_index_builder_sorts_family_pages_by_updated_then_title(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "older.md").write_text(
        "---\ntitle: Older\ntype: wiki\nstatus: active\nupdated: 2026-04-10\n---\n\n# Older\n",
        encoding="utf-8",
    )
    (wiki_root / "newer.md").write_text(
        "---\ntitle: Newer\ntype: wiki\nstatus: active\nupdated: 2026-04-12\n---\n\n# Newer\n",
        encoding="utf-8",
    )

    WikiIndexBuilder(tmp_path).refresh()

    projects_index = (tmp_path / "wiki" / "projects" / "index.md").read_text(encoding="utf-8").splitlines()
    page_lines = [line for line in projects_index if line.startswith("- [[wiki/projects/")]

    assert page_lines == [
        "- [[wiki/projects/newer|Newer]]",
        "- [[wiki/projects/older|Older]]",
    ]


def test_wiki_index_builder_prefers_claim_event_recency_over_frontmatter_updated(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / ".dory" / "claim-store.db")
    store.add_claim(
        entity_id="project:older",
        kind="state",
        statement="Older is active again.",
        evidence_path="sources/semantic/2026/04/14/older-write.md",
        occurred_at="2026-04-14T12:00:00Z",
    )
    store.add_claim(
        entity_id="project:newer",
        kind="state",
        statement="Newer has not changed recently.",
        evidence_path="sources/semantic/2026/04/12/newer-write.md",
        occurred_at="2026-04-12T12:00:00Z",
    )

    wiki_root = tmp_path / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "older.md").write_text(
        "---\ntitle: Older\ntype: wiki\nstatus: active\nupdated: 2026-04-01\n---\n\n# Older\n",
        encoding="utf-8",
    )
    (wiki_root / "newer.md").write_text(
        "---\ntitle: Newer\ntype: wiki\nstatus: active\nupdated: 2026-04-13\n---\n\n# Newer\n",
        encoding="utf-8",
    )

    WikiIndexBuilder(tmp_path).refresh()

    projects_index = (tmp_path / "wiki" / "projects" / "index.md").read_text(encoding="utf-8").splitlines()
    page_lines = [line for line in projects_index if line.startswith("- [[wiki/projects/")]

    assert page_lines == [
        "- [[wiki/projects/older|Older]]",
        "- [[wiki/projects/newer|Newer]]",
    ]

    hot_page = (tmp_path / "wiki" / "hot.md").read_text(encoding="utf-8")
    recent_pages_section = hot_page.split("## Recent Pages", 1)[1].split("## Active Threads", 1)[0]
    assert "- 2026-04-14: Older [projects]" in recent_pages_section
    assert "- 2026-04-12: Newer [projects]" in recent_pages_section


def test_memory_health_dashboard_ignores_wiki_indexes(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki"
    (wiki_root / "projects").mkdir(parents=True)
    (wiki_root / "index.md").write_text(
        "---\n"
        "title: Wiki\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "---\n\n"
        "# Wiki\n",
        encoding="utf-8",
    )
    (wiki_root / "projects" / "index.md").write_text(
        "---\n"
        "title: Projects\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "---\n\n"
        "# Projects\n",
        encoding="utf-8",
    )
    (wiki_root / "hot.md").write_text(
        "---\n"
        "title: Hot Cache\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "---\n\n"
        "# Recent Context\n",
        encoding="utf-8",
    )
    (wiki_root / "log.md").write_text(
        "---\n"
        "title: Activity Log\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "---\n\n"
        "# Activity Log\n",
        encoding="utf-8",
    )

    report = MemoryHealthDashboard(tmp_path).inspect()

    assert report["missing_evidence"] == []


def test_memory_health_dashboard_accepts_current_state_and_top_level_evidence_lists(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "rooster.md").write_text(
        "---\n"
        "title: Rooster\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "updated: 2026-04-14\n"
        "---\n\n"
        "# Rooster\n\n"
        "## Current State\n"
        "- Rooster is active.\n\n"
        "## Evidence\n"
        "- core/active.md\n",
        encoding="utf-8",
    )

    report = MemoryHealthDashboard(tmp_path).inspect()

    assert report["missing_evidence"] == []


def test_memory_health_dashboard_requires_real_evidence_refs(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "rooster.md").write_text(
        "---\n"
        "title: Rooster\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "updated: 2026-04-14\n"
        "---\n\n"
        "# Rooster\n\n"
        "## Current State\n"
        "- Rooster is active.\n\n"
        "## Evidence\n"
        "- Derived from claim store\n",
        encoding="utf-8",
    )

    report = MemoryHealthDashboard(tmp_path).inspect()

    assert report["missing_evidence"] == ["wiki/projects/rooster.md"]


def test_memory_health_dashboard_flags_missing_timeline_for_claim_style_page(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "rooster.md").write_text(
        "---\n"
        "title: Rooster\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "updated: 2026-04-14\n"
        "---\n\n"
        "# Rooster\n\n"
        "## Key claims\n"
        "- Rooster is active.\n\n"
        "## Evidence\n"
        "- sources/semantic/2026/04/14/rooster-write.md\n",
        encoding="utf-8",
    )

    report = MemoryHealthDashboard(tmp_path).inspect()

    assert report["missing_timeline"] == ["wiki/projects/rooster.md"]


def test_memory_health_dashboard_ignores_placeholder_contradiction_lines(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "rooster.md").write_text(
        "---\n"
        "title: Rooster\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "updated: 2026-04-14\n"
        "---\n\n"
        "# Rooster\n\n"
        "## Current State\n"
        "- Rooster is active.\n\n"
        "## Evidence\n"
        "- sources/semantic/2026/04/14/rooster-write.md\n\n"
        "## Timeline\n"
        "- 2026-04-14T00:00:00Z: Rooster is active.\n\n"
        "## Contradictions\n"
        "- No contradictions found.\n",
        encoding="utf-8",
    )

    report = MemoryHealthDashboard(tmp_path).inspect()

    assert report["contradictions"] == []


def test_memory_health_dashboard_flags_event_mismatch_between_timeline_and_evidence(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "rooster.md").write_text(
        "---\n"
        "title: Rooster\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "updated: 2026-04-14\n"
        "---\n\n"
        "# Rooster\n\n"
        "## Current State\n"
        "- Rooster is active.\n\n"
        "## Evidence\n"
        "### Added\n"
        "- sources/semantic/2026/04/14/rooster-write.md\n\n"
        "## Timeline\n"
        "- 2026-04-14T00:00:00Z: Retired: Rooster is active.\n",
        encoding="utf-8",
    )

    report = MemoryHealthDashboard(tmp_path).inspect()

    assert report["event_mismatch"] == ["wiki/projects/rooster.md"]


def test_memory_health_dashboard_flags_state_conflict_when_only_retired_events_exist(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "rooster.md").write_text(
        "---\n"
        "title: Rooster\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "updated: 2026-04-14\n"
        "---\n\n"
        "# Rooster\n\n"
        "## Current State\n"
        "- Rooster is active.\n\n"
        "## Evidence\n"
        "### Retired\n"
        "- sources/semantic/2026/04/14/rooster-forget.md\n\n"
        "## Timeline\n"
        "- 2026-04-14T00:00:00Z: Retired: Rooster is active.\n",
        encoding="utf-8",
    )

    report = MemoryHealthDashboard(tmp_path).inspect()

    assert report["state_conflict"] == ["wiki/projects/rooster.md"]


def test_memory_health_dashboard_flags_claim_mismatch_against_claim_store(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / ".dory" / "claim-store.db")
    store.add_claim(
        entity_id="project:rooster",
        kind="state",
        statement="Rooster is the active focus this week.",
        evidence_path="sources/semantic/2026/04/14/rooster-write.md",
    )
    wiki_root = tmp_path / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "rooster.md").write_text(
        "---\n"
        "title: Rooster\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "updated: 2026-04-14\n"
        "---\n\n"
        "# Rooster\n\n"
        "## Current State\n"
        "- Rooster is paused.\n\n"
        "## Evidence\n"
        "### Added\n"
        "- sources/semantic/2026/04/14/rooster-write.md\n\n"
        "## Timeline\n"
        "- 2026-04-14T00:00:00Z: Rooster is paused.\n",
        encoding="utf-8",
    )

    report = MemoryHealthDashboard(tmp_path).inspect()

    assert report["claim_mismatch"] == ["wiki/projects/rooster.md"]


def test_memory_health_dashboard_flags_claim_event_and_evidence_mismatch(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / ".dory" / "claim-store.db")
    store.add_claim(
        entity_id="project:rooster",
        kind="state",
        statement="Rooster is the active focus this week.",
        evidence_path="sources/semantic/2026/04/14/rooster-write.md",
    )
    store.retire_entity_claims(
        entity_id="project:rooster",
        reason="focus changed",
        evidence_path="sources/semantic/2026/04/14/rooster-forget.md",
    )
    wiki_root = tmp_path / "wiki" / "projects"
    wiki_root.mkdir(parents=True)
    (wiki_root / "rooster.md").write_text(
        "---\n"
        "title: Rooster\n"
        "type: wiki\n"
        "status: active\n"
        "canonical: true\n"
        "source_kind: generated\n"
        "temperature: warm\n"
        "updated: 2026-04-14\n"
        "---\n\n"
        "# Rooster\n\n"
        "## Current State\n"
        "- Rooster is the active focus this week.\n\n"
        "## Evidence\n"
        "### Added\n"
        "- sources/semantic/2026/04/14/rooster-write.md\n\n"
        "## Timeline\n"
        "- 2026-04-14T00:00:00Z: Added: Rooster is the active focus this week.\n",
        encoding="utf-8",
    )

    report = MemoryHealthDashboard(tmp_path).inspect()

    assert report["claim_event_mismatch"] == ["wiki/projects/rooster.md"]
    assert report["claim_evidence_mismatch"] == ["wiki/projects/rooster.md"]
