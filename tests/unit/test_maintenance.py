from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dory_core.frontmatter import load_markdown_document
from dory_core.maintenance import MaintenanceReportWriter, OpenRouterMaintenanceInspector, PrivacyMetadataBackfiller
from dory_core.maintenance import MemoryHealthDashboard


def test_maintenance_inspector_returns_structured_report(tmp_path) -> None:
    class FakeClient:
        def generate_json(self, **kwargs):
            return {
                "suggested_type": "project",
                "suggested_status": "active",
                "suggested_area": "coding",
                "suggested_canonical": True,
                "suggested_source_kind": "human",
                "suggested_temperature": "warm",
                "suggested_target": "projects/dory/state.md",
                "rationale": "The document describes current project state.",
                "confidence": 0.91,
            }

    inspector = OpenRouterMaintenanceInspector(client=FakeClient())  # type: ignore[arg-type]
    report = inspector.inspect(
        "knowledge/dev/dory-note.md",
        "---\ntitle: Dory note\ntype: knowledge\nstatus: pending\n---\n\nCurrent Dory rollout state.\n",
    )

    assert report.suggested_type == "project"
    assert report.suggested_target == "projects/dory/state.md"
    written = MaintenanceReportWriter(tmp_path).write(report)
    assert written.exists()
    assert written.name == "knowledge--dev--dory-note.json"


def test_memory_health_dashboard_flags_age_based_stale_pages(tmp_path) -> None:
    stale_updated = (datetime.now(tz=UTC).date() - timedelta(days=45)).isoformat()
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
        f"updated: {stale_updated}\n"
        "---\n\n"
        "# Rooster\n\n"
        "## Current State\n"
        "- Rooster is active.\n\n"
        "## Evidence\n"
        "- sources/semantic/2026/04/14/rooster-write.md\n\n"
        "## Timeline\n"
        f"- {stale_updated}T00:00:00Z: Added: Rooster is active.\n",
        encoding="utf-8",
    )

    report = MemoryHealthDashboard(tmp_path).inspect()

    assert report["stale_pages"] == ["wiki/projects/rooster.md"]


def test_memory_health_dashboard_flags_personal_docs_missing_privacy_metadata(tmp_path) -> None:
    personal_root = tmp_path / "knowledge" / "personal"
    personal_root.mkdir(parents=True)
    (personal_root / "raw-note.md").write_text(
        "---\n"
        "title: Raw personal note\n"
        "type: knowledge\n"
        "status: active\n"
        "canonical: false\n"
        "source_kind: imported\n"
        "---\n\n"
        "Private personal detail.\n",
        encoding="utf-8",
    )
    (personal_root / "labeled-note.md").write_text(
        "---\n"
        "title: Labeled personal note\n"
        "type: knowledge\n"
        "status: active\n"
        "visibility: private\n"
        "sensitivity: personal\n"
        "---\n\n"
        "Private personal detail with metadata.\n",
        encoding="utf-8",
    )

    report = MemoryHealthDashboard(tmp_path).inspect()

    assert report["missing_privacy_metadata"] == ["knowledge/personal/raw-note.md"]


def test_privacy_metadata_backfiller_dry_run_does_not_write(tmp_path) -> None:
    target = tmp_path / "knowledge" / "personal" / "raw-note.md"
    target.parent.mkdir(parents=True)
    original = (
        "---\n"
        "title: Raw personal note\n"
        "type: knowledge\n"
        "status: active\n"
        "---\n\n"
        "Private personal detail.\n"
    )
    target.write_text(original, encoding="utf-8")

    result = PrivacyMetadataBackfiller(tmp_path).run(dry_run=True)

    assert result.dry_run is True
    assert [change.path for change in result.changed] == ["knowledge/personal/raw-note.md"]
    assert result.changed[0].visibility == "private"
    assert result.changed[0].sensitivity == "personal"
    assert target.read_text(encoding="utf-8") == original


def test_privacy_metadata_backfiller_applies_minimal_frontmatter_patch(tmp_path) -> None:
    target = tmp_path / "inbox" / "config-note.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "---\n"
        "title: Config note\n"
        "type: capture\n"
        "status: raw\n"
        "tags:\n"
        "  - ops\n"
        "---\n\n"
        "Local auth token setup notes.\n",
        encoding="utf-8",
    )

    result = PrivacyMetadataBackfiller(tmp_path).run(paths=["inbox/config-note.md"], dry_run=False)
    document = load_markdown_document(target.read_text(encoding="utf-8"))

    assert result.changed[0].sensitivity == "credentials"
    assert document.frontmatter["visibility"] == "private"
    assert document.frontmatter["sensitivity"] == "credentials"
    assert document.body == "Local auth token setup notes.\n"
    assert "tags:\n  - ops\nvisibility: private\nsensitivity: credentials\n---" in target.read_text(encoding="utf-8")


def test_privacy_metadata_backfiller_preserves_existing_sensitivity(tmp_path) -> None:
    target = tmp_path / "people" / "person.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "---\n"
        "title: Person\n"
        "type: person\n"
        "sensitivity: contact\n"
        "---\n\n"
        "Contact detail.\n",
        encoding="utf-8",
    )

    result = PrivacyMetadataBackfiller(tmp_path).run(dry_run=False)
    document = load_markdown_document(target.read_text(encoding="utf-8"))

    assert result.changed[0].sensitivity == "contact"
    assert document.frontmatter["visibility"] == "private"
    assert document.frontmatter["sensitivity"] == "contact"


def test_privacy_metadata_backfiller_marks_non_sensitive_imported_docs_internal(tmp_path) -> None:
    target = tmp_path / "archive" / "knowledge" / "tool-report.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "---\n"
        "title: Tool report\n"
        "type: knowledge\n"
        "status: raw\n"
        "source_kind: legacy\n"
        "---\n\n"
        "Technical report about parser behavior.\n",
        encoding="utf-8",
    )

    result = PrivacyMetadataBackfiller(tmp_path).run(dry_run=False)
    document = load_markdown_document(target.read_text(encoding="utf-8"))

    assert result.changed[0].visibility == "internal"
    assert result.changed[0].sensitivity == "none"
    assert document.frontmatter["visibility"] == "internal"
    assert document.frontmatter["sensitivity"] == "none"


def test_privacy_metadata_backfiller_uses_existing_health_report(tmp_path) -> None:
    target = tmp_path / "knowledge" / "personal" / "raw-note.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "---\n"
        "title: Raw personal note\n"
        "type: knowledge\n"
        "---\n\n"
        "Private personal detail.\n",
        encoding="utf-8",
    )
    report = tmp_path / "inbox" / "maintenance" / "wiki-health.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        '{"missing_privacy_metadata": ["knowledge/personal/raw-note.md"]}\n',
        encoding="utf-8",
    )

    result = PrivacyMetadataBackfiller(tmp_path).run(dry_run=True)

    assert [change.path for change in result.changed] == ["knowledge/personal/raw-note.md"]
