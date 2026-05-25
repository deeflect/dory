from __future__ import annotations

from pathlib import Path

import pytest

from dory_core.entity_context import (
    EntityContext,
    resolve_default_entity_context,
    resolve_entity_context,
)
from dory_core.entity_registry import EntityRegistry
from dory_core.subject_resolver import SubjectResolver
from dory_core.types import ActiveMemoryReq


# ---------------------------------------------------------------------------
# EntityContext dataclass
# ---------------------------------------------------------------------------


class TestEntityContextDataclass:
    def test_frozen(self) -> None:
        ctx = EntityContext(
            entity_id="project:palace",
            canonical_name="Palace",
            family="project",
            canonical_path="projects/palace/state.md",
            matched_by="project_handle",
            source_refs=("projects/palace/state.md",),
        )
        with pytest.raises(AttributeError):
            ctx.entity_id = "other"  # type: ignore[misc]

    def test_slots(self) -> None:
        ctx = EntityContext(
            entity_id="person:avery-z",
            canonical_name="Avery Z",
            family="person",
            canonical_path="people/avery-z.md",
            matched_by="alias",
            source_refs=("people/avery-z.md",),
        )
        with pytest.raises((AttributeError, TypeError)):
            ctx.new_attr = "nope"  # type: ignore[attr-defined]

    def test_empty_source_refs(self) -> None:
        ctx = EntityContext(
            entity_id="project:unknown",
            canonical_name="Unknown",
            family="project",
            canonical_path=None,
            matched_by="project_handle",
            source_refs=(),
        )
        assert ctx.source_refs == ()
        assert ctx.canonical_path is None

    def test_repr(self) -> None:
        ctx = EntityContext(
            entity_id="concept:test",
            canonical_name="Test",
            family="concept",
            canonical_path="concepts/test.md",
            matched_by="title",
            source_refs=("concepts/test.md",),
        )
        r = repr(ctx)
        assert "EntityContext" in r
        assert "entity_id='concept:test'" in r


# ---------------------------------------------------------------------------
# resolve_entity_context
# ---------------------------------------------------------------------------


class TestResolveEntityContext:
    def test_empty_subject_returns_none(self) -> None:
        assert resolve_entity_context("") is None
        assert resolve_entity_context("   ") is None

    def test_resolve_via_registry(self, tmp_path: Path) -> None:
        registry = EntityRegistry(tmp_path / "entities.db")
        registry.upsert(
            entity_id="person:avery-z",
            family="person",
            title="Avery Z",
            target_path="people/avery-z.md",
            aliases=("avery",),
        )

        ctx = resolve_entity_context("avery", family="person", registry=registry)
        assert ctx is not None
        assert ctx.entity_id == "person:avery-z"
        assert ctx.canonical_name == "Avery Z"
        assert ctx.family == "person"
        assert ctx.canonical_path == "people/avery-z.md"
        assert ctx.matched_by == "alias"
        assert "people/avery-z.md" in ctx.source_refs

    def test_resolve_via_registry_title_match(self, tmp_path: Path) -> None:
        registry = EntityRegistry(tmp_path / "entities.db")
        registry.upsert(
            entity_id="person:bob",
            family="person",
            title="Bob Smith",
            target_path="people/bob-smith.md",
            aliases=("bob",),
        )

        ctx = resolve_entity_context("Bob Smith", registry=registry)
        assert ctx is not None
        assert ctx.entity_id == "person:bob"
        assert ctx.matched_by == "title"

    def test_resolve_via_registry_no_match_returns_none(self, tmp_path: Path) -> None:
        registry = EntityRegistry(tmp_path / "entities.db")
        registry.upsert(
            entity_id="project:palace",
            family="project",
            title="Palace",
            target_path="projects/palace/state.md",
        )

        ctx = resolve_entity_context("nonexistent", registry=registry)
        assert ctx is None

    def test_resolve_via_subject_resolver(self, tmp_path: Path) -> None:
        """SubjectResolver picks up markdown files with frontmatter."""
        (tmp_path / "people").mkdir()
        (tmp_path / "people" / "alex.md").write_text(
            "---\ntitle: Alex\n---\n\nAlex's notes.\n",
            encoding="utf-8",
        )
        resolver = SubjectResolver(tmp_path)

        ctx = resolve_entity_context("Alex", subject_resolver=resolver)
        assert ctx is not None
        assert ctx.entity_id == "person:alex"
        assert ctx.canonical_name == "Alex"
        assert ctx.family == "person"
        # SubjectResolver exact match on slug returns "subject_ref"
        assert ctx.matched_by == "subject_ref"
        assert ctx.canonical_path == "people/alex.md"

    def test_resolve_via_subject_resolver_no_match(self, tmp_path: Path) -> None:
        resolver = SubjectResolver(tmp_path)
        ctx = resolve_entity_context("Ghost", subject_resolver=resolver)
        assert ctx is None

    def test_resolve_subject_resolver_fallback_after_registry_miss(
        self, tmp_path: Path,
    ) -> None:
        registry = EntityRegistry(tmp_path / "entities.db")
        (tmp_path / "people").mkdir()
        (tmp_path / "people" / "casey.md").write_text(
            "---\ntitle: Casey\n---\n\nCasey's page.\n",
            encoding="utf-8",
        )
        resolver = SubjectResolver(tmp_path)

        # Registry miss -> subject resolver hit
        ctx = resolve_entity_context("Casey", registry=registry, subject_resolver=resolver)
        assert ctx is not None
        assert ctx.entity_id == "person:casey"
        assert ctx.family == "person"

    def test_resolve_project_from_handle(self, tmp_path: Path) -> None:
        (tmp_path / "projects" / "dory" / "state.md").parent.mkdir(parents=True)
        (tmp_path / "projects" / "dory" / "state.md").write_text(
            "---\ntitle: Dory\ntype: project\nstatus: active\n---\n\nDory project.\n",
            encoding="utf-8",
        )

        ctx = resolve_entity_context("dory", root=tmp_path)
        assert ctx is not None
        assert ctx.entity_id == "project:dory"
        assert ctx.canonical_name == "Dory"
        assert ctx.family == "project"
        assert ctx.matched_by == "project_handle"
        assert "projects/dory/state.md" in ctx.source_refs

    def test_resolve_project_no_root_returns_none(self) -> None:
        ctx = resolve_entity_context("dory", root=None)
        assert ctx is None

    def test_resolve_project_nonexistent_returns_none(self, tmp_path: Path) -> None:
        ctx = resolve_entity_context("nonexistent-project", root=tmp_path)
        assert ctx is None

    def test_registry_takes_priority_over_subject_resolver(self, tmp_path: Path) -> None:
        registry = EntityRegistry(tmp_path / "entities.db")
        registry.upsert(
            entity_id="person:duplicate",
            family="person",
            title="Duplicate Entry",
            target_path="people/duplicate-registry.md",
        )
        (tmp_path / "people").mkdir()
        (tmp_path / "people" / "duplicate.md").write_text(
            "---\ntitle: Duplicate Entry\n---\n\nFile system entry.\n",
            encoding="utf-8",
        )
        resolver = SubjectResolver(tmp_path)

        ctx = resolve_entity_context("Duplicate Entry", registry=registry, subject_resolver=resolver)
        assert ctx is not None
        # Registry should win (deterministic, SQLite-backed)
        assert ctx.entity_id == "person:duplicate"
        assert ctx.canonical_path == "people/duplicate-registry.md"
        assert ctx.matched_by == "title"

    def test_family_filter_limits_to_family(self, tmp_path: Path) -> None:
        registry = EntityRegistry(tmp_path / "entities.db")
        registry.upsert(
            entity_id="concept:dory",
            family="concept",
            title="Dory",
            target_path="concepts/dory.md",
        )
        registry.upsert(
            entity_id="project:dory",
            family="project",
            title="Dory Project",
            target_path="projects/dory/state.md",
        )

        # With family="project", should only match project entity
        ctx = resolve_entity_context("Dory Project", family="project", registry=registry)
        assert ctx is not None
        assert ctx.entity_id == "project:dory"
        assert ctx.family == "project"

    def test_no_registry_or_resolver_returns_none(self, tmp_path: Path) -> None:
        # No registry, no subject resolver, no project root
        ctx = resolve_entity_context("anything", root=None)
        assert ctx is None


# ---------------------------------------------------------------------------
# resolve_default_entity_context
# ---------------------------------------------------------------------------


class TestResolveDefaultEntityContext:
    def test_explicit_project(self, tmp_path: Path) -> None:
        (tmp_path / "projects" / "palace" / "state.md").parent.mkdir(parents=True)
        (tmp_path / "projects" / "palace" / "state.md").write_text(
            "---\ntitle: Palace\ntype: project\n---\n",
            encoding="utf-8",
        )
        ctx = resolve_default_entity_context(project="palace", root=tmp_path)
        assert ctx is not None
        assert ctx.entity_id == "project:palace"

    def test_explicit_project_nonexistent_returns_none(self, tmp_path: Path) -> None:
        ctx = resolve_default_entity_context(project="nonexistent", root=tmp_path)
        assert ctx is None

    def test_infer_from_cwd(self, tmp_path: Path) -> None:
        (tmp_path / "projects" / "dory" / "state.md").parent.mkdir(parents=True)
        (tmp_path / "projects" / "dory" / "state.md").write_text(
            "---\ntitle: Dory\ntype: project\n---\n",
            encoding="utf-8",
        )
        # Simulate cwd inside a project-like directory
        ctx = resolve_default_entity_context(cwd=str(tmp_path / "projects" / "dory"), root=tmp_path)
        assert ctx is not None
        assert ctx.entity_id == "project:dory"

    def test_infer_from_cwd_with_pyproject(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "pyproject.toml").write_text(
            '[project]\nname = "palace"\n', encoding="utf-8"
        )
        (tmp_path / "projects" / "palace" / "state.md").parent.mkdir(parents=True)
        (tmp_path / "projects" / "palace" / "state.md").write_text(
            "---\ntitle: Palace\ntype: project\n---\n",
            encoding="utf-8",
        )

        ctx = resolve_default_entity_context(cwd=str(workspace), root=tmp_path)
        assert ctx is not None
        assert ctx.entity_id == "project:palace"

    def test_no_project_or_cwd_returns_none(self, tmp_path: Path) -> None:
        ctx = resolve_default_entity_context(root=tmp_path)
        assert ctx is None

    def test_no_root_returns_none(self) -> None:
        ctx = resolve_default_entity_context(project="palace", root=None)
        assert ctx is None


# ---------------------------------------------------------------------------
# Integration: entity context with active memory response shape
# ---------------------------------------------------------------------------


class TestEntityContextInActiveMemoryResponse:
    """Verify that the ActiveMemoryResp can carry entity_context."""

    def test_response_with_entity_context(self) -> None:
        from dory_core.types import ActiveMemoryResp

        resp = ActiveMemoryResp(
            kind="memory",
            block="## Active\n\nCurrent focus.",
            summary="Current focus.",
            entity_context={
                "entity_id": "project:dory",
                "canonical_name": "Dory",
                "family": "project",
                "canonical_path": "projects/dory/state.md",
                "matched_by": "project_handle",
                "source_refs": ["projects/dory/state.md"],
            },
        )
        assert resp.entity_context is not None
        assert resp.entity_context["entity_id"] == "project:dory"
        assert resp.entity_context["family"] == "project"

    def test_response_without_entity_context(self) -> None:
        from dory_core.types import ActiveMemoryResp

        resp = ActiveMemoryResp(kind="none", block="", summary="")
        assert resp.entity_context is None

    @pytest.mark.parametrize("resolve_flag", [True, False])
    def test_req_has_resolve_entity_context_flag(self, resolve_flag: bool) -> None:
        req = ActiveMemoryReq(prompt="test", agent="test", resolve_entity_context=resolve_flag)
        assert req.resolve_entity_context is resolve_flag
