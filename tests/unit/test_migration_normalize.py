from __future__ import annotations

import pytest

from dory_core.frontmatter import load_markdown_document
from dory_core.migration_types import ClassifiedDocument
from dory_core.migration_normalize import (
    canonical_target_for_subject,
    concept_kind_for_legacy_path,
    normalize_classification_target,
    normalize_migration_slug,
    render_canonical_template,
    render_core_template,
)
from dory_core.schema import TIMELINE_MARKER


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Casey Party / Alpha__Beta", "casey-party-alpha-beta"),
        ("  Rooster Spec  ", "rooster-spec"),
        ("OpenClaw Memory", "openclaw-memory"),
    ],
)
def test_normalize_migration_slug(value: str, expected: str) -> None:
    assert normalize_migration_slug(value) == expected


@pytest.mark.parametrize(
    ("subject_ref", "expected"),
    [
        ("person:Alex Example", "people/alex-example.md"),
        ("project:Rooster Spec", "projects/rooster-spec/state.md"),
        ("concept:OpenClaw Memory", "concepts/openclaw-memory.md"),
        ("decision:Active Memory", "decisions/active-memory.md"),
        ("people:Anna", "people/anna.md"),
        ("projects:Content Command Center", "projects/content-command-center/state.md"),
    ],
)
def test_canonical_target_for_subject(subject_ref: str, expected: str) -> None:
    assert canonical_target_for_subject(subject_ref) == expected


def test_concept_kind_for_legacy_path() -> None:
    assert concept_kind_for_legacy_path("memory/tools/openclaw.md") == "tool"
    assert concept_kind_for_legacy_path("memory/health/profile.md") == "health"
    assert concept_kind_for_legacy_path("memory/projects/rooster-spec.md") == "general"


def test_render_canonical_template_has_required_sections() -> None:
    rendered = render_canonical_template(
        family="project",
        title="Rooster",
        slug="rooster",
        domain="work",
    )
    doc = load_markdown_document(rendered)

    assert doc.frontmatter["title"] == "Rooster"
    assert doc.frontmatter["type"] == "project"
    assert doc.frontmatter["slug"] == "rooster"
    assert doc.frontmatter["domain"] == "work"
    assert doc.frontmatter["canonical"] is True
    assert doc.frontmatter["source_kind"] == "canonical"
    assert doc.frontmatter["confidence"] == "high"
    assert doc.frontmatter["aliases"] == []
    assert "## Summary" in doc.body
    assert "## Current State" in doc.body
    assert "## Goals" in doc.body
    assert "## Timeline" in doc.body
    assert "## Evidence" in doc.body
    assert TIMELINE_MARKER in doc.body


def test_render_core_template_has_expected_sections() -> None:
    rendered = render_core_template(file_name="user.md", title="Casey", domain="personal")
    doc = load_markdown_document(rendered)

    assert doc.frontmatter["type"] == "core"
    assert doc.frontmatter["slug"] == "user"
    assert doc.frontmatter["domain"] == "personal"
    assert doc.frontmatter["aliases"] == []
    assert "## Summary" in doc.body
    assert "## Constraints" in doc.body
    assert "## Timeline" in doc.body
    assert "## Evidence" in doc.body


def test_normalize_classification_target_forces_supported_core_and_canonical_paths() -> None:
    core = ClassifiedDocument(
        doc_class="core_user",
        canonicality="canonical",
        target_path="core/borb_persona.md",
        domain="mixed",
        entity_refs=(),
        decision_refs=(),
        time_scope="current",
        confidence="high",
        action="route_final",
        reason="test",
    )
    project = ClassifiedDocument(
        doc_class="project_state",
        canonicality="canonical",
        target_path="notes/rooster plan.md",
        domain="work",
        entity_refs=("project:Rooster",),
        decision_refs=(),
        time_scope="current",
        confidence="high",
        action="route_final",
        reason="test",
    )

    assert normalize_classification_target(core).target_path == "core/user.md"
    assert normalize_classification_target(project).target_path == "projects/rooster/state.md"
