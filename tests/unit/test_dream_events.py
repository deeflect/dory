from __future__ import annotations

from datetime import UTC, datetime

from dory_core.dreaming.events import SessionClosedEvent


def test_session_closed_event_targets_distilled_output_path() -> None:
    event = SessionClosedEvent(
        agent="claude-code",
        session_path="logs/sessions/claude-code/2026-04-07.md",
        closed_at=datetime(2026, 4, 7, 20, 30, tzinfo=UTC),
    )

    assert event.output_path == "inbox/distilled/claude-code-2026-04-07.md"
