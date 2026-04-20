from __future__ import annotations

from pathlib import Path

from dory_core.session_plane import SessionEvidencePlane, SessionSearchQuery


def test_session_plane_upserts_and_searches_recent_entries(tmp_path: Path) -> None:
    plane = SessionEvidencePlane(db_path=tmp_path / "sessions.db")
    plane.upsert_session_chunk(
        path="logs/sessions/claude/macbook/2026-04-12-s1.md",
        content="We decided to use Rooster as the active focus this week.",
        updated="2026-04-12T10:00:00Z",
        agent="claude",
        device="macbook",
        session_id="s1",
        status="active",
    )

    result = plane.search(SessionSearchQuery(query="active focus Rooster", limit=5))

    assert result.count == 1
    assert result.results[0].path == "logs/sessions/claude/macbook/2026-04-12-s1.md"
    assert "Rooster" in result.results[0].snippet


def test_session_plane_prefers_newer_matching_session(tmp_path: Path) -> None:
    plane = SessionEvidencePlane(db_path=tmp_path / "sessions.db")
    plane.upsert_session_chunk(
        path="logs/sessions/codex/mini/2026-04-10-a.md",
        content="We cleaned up SOUL on Friday.",
        updated="2026-04-10T09:00:00Z",
        agent="codex",
        device="mini",
        session_id="old",
        status="done",
    )
    plane.upsert_session_chunk(
        path="logs/sessions/claude/macbook/2026-04-12-b.md",
        content="We cleaned up SOUL yesterday and updated core files.",
        updated="2026-04-12T09:00:00Z",
        agent="claude",
        device="macbook",
        session_id="new",
        status="active",
    )

    result = plane.search(SessionSearchQuery(query="cleaned up SOUL", limit=5))

    assert result.results[0].session_id == "new"


def test_session_plane_updates_search_index_without_full_rebuild(tmp_path: Path) -> None:
    plane = SessionEvidencePlane(db_path=tmp_path / "sessions.db")
    path = "logs/sessions/claude/macbook/2026-04-12-s1.md"
    plane.upsert_session_chunk(
        path=path,
        content="We are focused on Rooster this week.",
        updated="2026-04-12T10:00:00Z",
        agent="claude",
        device="macbook",
        session_id="s1",
        status="active",
    )

    first = plane.search(SessionSearchQuery(query="Rooster", limit=5))
    assert first.count == 1

    plane.upsert_session_chunk(
        path=path,
        content="We are focused on Tolk this week.",
        updated="2026-04-12T10:05:00Z",
        agent="claude",
        device="macbook",
        session_id="s1",
        status="active",
    )

    updated = plane.search(SessionSearchQuery(query="Tolk", limit=5))
    stale = plane.search(SessionSearchQuery(query="Rooster", limit=5))

    assert updated.count == 1
    assert updated.results[0].path == path
    assert stale.count == 0


def test_session_plane_prefers_phrase_match_over_loose_token_match(tmp_path: Path) -> None:
    plane = SessionEvidencePlane(db_path=tmp_path / "sessions.db")
    plane.upsert_session_chunk(
        path="logs/sessions/claude/macbook/2026-04-10-a.md",
        content="We chose active memory staging for the reply flow.",
        updated="2026-04-10T09:00:00Z",
        agent="claude",
        device="macbook",
        session_id="phrase",
        status="done",
    )
    plane.upsert_session_chunk(
        path="logs/sessions/claude/macbook/2026-04-12-b.md",
        content="This note says active and later mentions memory in another place.",
        updated="2026-04-12T09:00:00Z",
        agent="claude",
        device="macbook",
        session_id="loose",
        status="active",
    )

    result = plane.search(SessionSearchQuery(query="active memory", limit=5))

    assert result.results[0].session_id == "phrase"
