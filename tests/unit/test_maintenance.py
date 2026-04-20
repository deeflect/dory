from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dory_core.maintenance import MaintenanceReportWriter, OpenRouterMaintenanceInspector
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
