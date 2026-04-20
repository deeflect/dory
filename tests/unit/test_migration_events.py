from __future__ import annotations

from dataclasses import asdict

from dory_core.migration_events import MigrationRunEvent


def test_migration_run_event_serializes_counters() -> None:
    event = MigrationRunEvent(
        kind="file_classified",
        phase="classify",
        processed_count=3,
        total_count=10,
        path="memory/daily/a.md",
        message="Processed memory/daily/a.md",
        llm_classified_count=2,
        fallback_classified_count=1,
    )

    payload = asdict(event)

    assert payload["kind"] == "file_classified"
    assert payload["phase"] == "classify"
    assert payload["processed_count"] == 3
    assert payload["total_count"] == 10
    assert payload["path"] == "memory/daily/a.md"
    assert payload["message"] == "Processed memory/daily/a.md"
    assert payload["llm_classified_count"] == 2
    assert payload["fallback_classified_count"] == 1
    assert payload["written_count"] == 0
