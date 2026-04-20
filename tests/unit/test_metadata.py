from __future__ import annotations

from pathlib import Path

import pytest

from dory_core.errors import DoryValidationError
from dory_core.metadata import (
    infer_target_from_frontmatter,
    normalize_doc_type,
    normalize_frontmatter,
    plan_migration_path,
    resolve_write_target,
)


def test_normalize_frontmatter_maps_legacy_capture_values() -> None:
    normalized = normalize_frontmatter(
        {
            "title": "Inbox note",
            "type": "inbox",
            "status": "inbox",
        },
        target=Path("inbox/inbox-note.md"),
    )

    assert normalized["type"] == "capture"
    assert normalized["status"] == "raw"
    assert normalized["canonical"] is False
    assert normalized["temperature"] == "cold"


def test_normalize_frontmatter_infers_type_from_archived_daily_path() -> None:
    normalized = normalize_frontmatter(
        {
            "title": "Archived day",
            "type": "archive",
            "status": "archived",
            "date": "2026-02-09",
        },
        target=Path("archive/daily/2026-02-09-digest.md"),
    )

    assert normalized["type"] == "daily"
    assert normalized["status"] == "superseded"


def test_resolve_write_target_rejects_mismatched_bucket() -> None:
    with pytest.raises(DoryValidationError):
        resolve_write_target(
            "knowledge/meeting.md",
            frontmatter={"title": "Meeting", "type": "project"},
        )


def test_plan_migration_path_dissolves_archive_ideas_by_role() -> None:
    migration = plan_migration_path(
        Path("archive/ideas/2026-03-16-authority-building-masterplan.md"),
        {
            "title": "Operation: Casey Everywhere",
            "type": "project",
            "created": "2026-03-16",
        },
    )

    assert migration.unresolved_reason is None
    assert migration.path == Path("projects/2026-03-16-authority-building-masterplan/state.md")


def test_plan_migration_path_moves_archive_tweets_into_references() -> None:
    migration = plan_migration_path(
        Path("archive/tweets/2026-03-01.md"),
        {
            "title": "Tweet Digest",
            "type": "tweet-digest",
        },
    )

    assert migration.unresolved_reason is None
    assert migration.path == Path("references/tweets/2026-03-01.md")


def test_idea_type_routes_to_ideas_bucket() -> None:
    target = infer_target_from_frontmatter(
        {"title": "Casey CEO site", "type": "idea"},
        filename_hint="2026-02-19-casey-ceo.md",
    )

    assert target == Path("ideas/2026-02-19-casey-ceo.md")


def test_idea_type_defaults() -> None:
    normalized = normalize_frontmatter(
        {"title": "ADHD humanize", "type": "idea"},
        target=Path("ideas/2026-04-15-adhd-humanize.md"),
    )

    assert normalized["type"] == "idea"
    assert normalized["status"] == "pending"
    assert normalized["canonical"] is False
    assert normalized["temperature"] == "warm"
    assert normalized["source_kind"] == "human"


def test_normalize_frontmatter_validates_privacy_metadata() -> None:
    normalized = normalize_frontmatter(
        {
            "title": "Private note",
            "type": "knowledge",
            "visibility": "PRIVATE",
            "sensitivity": "Legal",
        },
        target=Path("knowledge/personal/private-note.md"),
    )

    assert normalized["visibility"] == "private"
    assert normalized["sensitivity"] == "legal"


def test_normalize_frontmatter_rejects_invalid_privacy_metadata() -> None:
    with pytest.raises(DoryValidationError):
        normalize_frontmatter(
            {
                "title": "Private note",
                "type": "knowledge",
                "visibility": "friends-only",
            },
            target=Path("knowledge/personal/private-note.md"),
        )


def test_polluted_type_value_with_comment_sanitizes() -> None:
    assert normalize_doc_type("product  # product, content, infra") == "project"


def test_polluted_type_value_with_pipe_sanitizes() -> None:
    assert normalize_doc_type("idea | project | knowledge | saved") == "idea"


def test_polluted_type_value_with_em_dash_sanitizes() -> None:
    assert normalize_doc_type("knowledge — personal notes") == "knowledge"


def test_unknown_type_after_sanitize_still_raises() -> None:
    with pytest.raises(DoryValidationError):
        normalize_doc_type("enum — preference | memory")


def test_new_type_aliases_resolve() -> None:
    assert normalize_doc_type("saved") == "knowledge"
    assert normalize_doc_type("link") == "reference"
    assert normalize_doc_type("note") == "note"
    assert normalize_doc_type("strategy") == "report"
    assert normalize_doc_type("analysis") == "report"
    assert normalize_doc_type("digest") == "digest-daily"
    assert normalize_doc_type("content-idea") == "idea"
    assert normalize_doc_type("tweet-idea") == "idea"
    assert normalize_doc_type("strategic-idea") == "idea"


def test_plan_migration_path_preserves_ideas_bucket() -> None:
    migration = plan_migration_path(
        Path("ideas/2026-02-19-casey-ceo.md"),
        {"title": "Casey CEO", "type": "idea"},
    )

    assert migration.unresolved_reason is None
    assert migration.path == Path("ideas/2026-02-19-casey-ceo.md")
