from __future__ import annotations

from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.observation_retrieval import ObservationRetrieval
from dory_core.observation_store import ObservationStore
from dory_core.semantic_write import SemanticWriteEngine, SubjectMatch, SubjectResolver, build_semantic_write_plan
from dory_core.semantic_write_artifacts import SemanticEvidenceStore
from dory_core.semantic_write_plan import (
    is_canonical_semantic_target,
    primary_section_for_plan,
    semantic_write_preview_message,
    semantic_write_preview_payload,
)
from dory_core.types import MemoryWriteReq, MemoryWriteResp


def test_memory_write_req_accepts_semantic_fields() -> None:
    req = MemoryWriteReq(
        action="write",
        kind="decision",
        subject="sample",
        content="Sample is the active focus this week.",
    )

    assert req.action == "write"
    assert req.kind == "decision"
    assert req.subject == "sample"
    assert req.soft is False


def test_memory_write_resp_carries_resolution_metadata() -> None:
    resp = MemoryWriteResp(
        resolved=True,
        action="write",
        kind="decision",
        subject_ref="project:sample",
        target_path="projects/sample/state.md",
        result="written",
        confidence="high",
        indexed=True,
        quarantined=False,
    )

    assert resp.subject_ref == "project:sample"
    assert resp.target_path == "projects/sample/state.md"
    assert resp.result == "written"
    assert resp.indexed is True
    assert resp.quarantined is False


def test_semantic_write_preview_includes_provenance_and_plan(tmp_path: Path) -> None:
    (tmp_path / "projects" / "sample").mkdir(parents=True)
    (tmp_path / "projects" / "sample" / "state.md").write_text(
        "---\ntitle: Sample\n---\n# Sample\n",
        encoding="utf-8",
    )
    engine = SemanticWriteEngine(tmp_path, resolver_client=None)

    resp = engine.write(
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="sample",
            content="Sample is the active focus this week.",
            scope="project",
            dry_run=True,
            agent="codex",
            session_id="session-1",
            origin_surface="mcp",
        )
    )

    assert resp.result == "preview"
    assert resp.evidence_path is not None
    assert resp.matched_by == "subject_ref"
    assert resp.preview is not None
    assert resp.preview["target_path"] == "projects/sample/state.md"
    assert resp.preview["evidence_path"] == resp.evidence_path


def test_semantic_write_does_not_create_observation_store_by_default(tmp_path: Path) -> None:
    engine = SemanticWriteEngine(tmp_path, resolver_client=None)

    engine.write(
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="Dory",
            content="Dory write path should avoid accidental observation side effects.",
            scope="project",
            allow_canonical=True,
        )
    )

    assert not (tmp_path / ".dory" / "observation-store.db").exists()


def test_semantic_write_refreshes_existing_observation_store(tmp_path: Path) -> None:
    ObservationStore(tmp_path / ".dory" / "observation-store.db")
    engine = SemanticWriteEngine(tmp_path, resolver_client=None)

    engine.write(
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="Dory",
            content="Dory writes refresh the observation index when it exists.",
            scope="project",
            project="dory",
            allow_canonical=True,
        )
    )

    retrieval = ObservationRetrieval(ObservationStore(tmp_path / ".dory" / "observation-store.db"))
    observations = retrieval.find_by_entity("project:dory")
    assert len(observations) == 1
    assert observations[0].content == "Dory writes refresh the observation index when it exists."


def test_semantic_write_plan_preview_helpers_describe_canonical_target(tmp_path: Path) -> None:
    plan = build_semantic_write_plan(
        tmp_path,
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="Open Privacy Filter",
            content="Open Privacy Filter is active.",
            scope="project",
            project="open-privacy-filter",
        ),
    )

    message = semantic_write_preview_message(
        plan,
        action="would_create",
        evidence_path="sources/semantic/2026/05/25/open-privacy-filter-write.md",
    )
    payload = semantic_write_preview_payload(
        plan,
        action="would_create",
        evidence_path="sources/semantic/2026/05/25/open-privacy-filter-write.md",
    )

    assert is_canonical_semantic_target(plan) is True
    assert primary_section_for_plan(plan) == "Current State"
    assert "CANONICAL TARGET projects/open-privacy-filter/state.md" in message
    assert payload == {
        "action": "would_create",
        "subject": "Open Privacy Filter",
        "subject_ref": "project:open-privacy-filter",
        "target_subject_ref": "project:open-privacy-filter",
        "target_path": "projects/open-privacy-filter/state.md",
        "family": "project",
        "kind": "state",
        "resolved_mode": "append",
        "matched_by": "explicit_scope",
        "match_confidence": "high",
        "evidence_path": "sources/semantic/2026/05/25/open-privacy-filter-write.md",
        "canonical_target": True,
    }


def test_semantic_evidence_store_plans_and_writes_artifact(tmp_path: Path) -> None:
    plan = build_semantic_write_plan(
        tmp_path,
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="Open Privacy Filter",
            content="Open Privacy Filter is active.",
            scope="project",
            agent="codex",
            session_id="session-1",
            origin_surface="unit-test",
        ),
    )
    store = SemanticEvidenceStore(tmp_path)

    artifact = store.plan(plan)
    store.write(artifact)

    assert artifact.path.startswith("sources/semantic/")
    assert artifact.path.endswith("-write.md")
    assert "open-privacy-filter" in artifact.path
    document = load_markdown_document((tmp_path / artifact.path).read_text(encoding="utf-8"))
    assert document.body == "Open Privacy Filter is active.\n"
    assert document.frontmatter["source_kind"] == "semantic"
    assert document.frontmatter["entity_id"] == "project:open-privacy-filter"
    assert document.frontmatter["canonical_target"] == "projects/open-privacy-filter/state.md"
    assert document.frontmatter["origin_surface"] == "unit-test"
    assert document.frontmatter["agent"] == "codex"
    assert document.frontmatter["session_id"] == "session-1"


def test_semantic_write_reuses_existing_evidence_for_idempotent_replay(tmp_path: Path) -> None:
    engine = SemanticWriteEngine(tmp_path, resolver_client=None)
    req = MemoryWriteReq(
        action="write",
        kind="state",
        subject="Open Privacy Filter",
        content="Open Privacy Filter is active in characterization tests.",
        scope="project",
        project="open-privacy-filter",
        allow_canonical=True,
        agent="codex",
        session_id="session-1",
        origin_surface="unit-test",
    )

    first = engine.write(req)
    second = engine.write(req)

    assert first.result == "written"
    assert first.evidence_path is not None
    assert second.result == "written"
    assert second.indexed is False
    assert second.quarantined is False
    assert second.evidence_path == first.evidence_path
    assert second.message == "idempotent semantic write replay; existing evidence reused"


def test_subject_resolver_matches_aliases_titles_and_fuzzy_subjects(tmp_path: Path) -> None:
    (tmp_path / "people").mkdir(parents=True)
    (tmp_path / "projects" / "sample").mkdir(parents=True)
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "concepts").mkdir(parents=True)
    (tmp_path / "decisions").mkdir(parents=True)

    (tmp_path / "people" / "alex-example.md").write_text(
        "---\ntitle: Alex Example\naliases:\n  - avery\n---\n# Avery\n",
        encoding="utf-8",
    )
    (tmp_path / "projects" / "sample" / "state.md").write_text(
        "---\ntitle: Sample\naliases:\n  - sample project\n---\n# Sample\n",
        encoding="utf-8",
    )
    (tmp_path / "core" / "user.md").write_text(
        "---\ntitle: User\naliases:\n  - profile\n---\n# User\n",
        encoding="utf-8",
    )
    (tmp_path / "decisions" / "postgres-first.md").write_text(
        "---\ntitle: Postgres First\naliases:\n  - postgres decision\n---\n# Postgres First\n",
        encoding="utf-8",
    )

    resolver = SubjectResolver(tmp_path)

    assert resolver.resolve("avery").subject_ref == "person:alex-example"
    assert resolver.resolve("Sample project").subject_ref == "project:sample"
    assert resolver.resolve("user", scope="core").subject_ref == "core:user"
    assert resolver.resolve("postgres decision").subject_ref == "decision:postgres-first"


def test_subject_resolver_matches_date_prefixed_decision_suffixes(tmp_path: Path) -> None:
    (tmp_path / "decisions").mkdir(parents=True)
    (tmp_path / "decisions" / "2026-04-07-homeserver.md").write_text(
        "---\ntitle: HomeServer Host\n---\n# HomeServer\n",
        encoding="utf-8",
    )

    resolver = SubjectResolver(tmp_path)

    assert resolver.resolve("homeserver", scope="decision").subject_ref == "decision:2026-04-07-homeserver"


def test_build_semantic_write_plan_routes_to_canonical_targets(tmp_path: Path) -> None:
    (tmp_path / "projects" / "sample").mkdir(parents=True)
    (tmp_path / "projects" / "sample" / "state.md").write_text(
        "---\ntitle: Sample\n---\n# Sample\n",
        encoding="utf-8",
    )
    (tmp_path / "core").mkdir(parents=True)
    (tmp_path / "core" / "user.md").write_text(
        "---\ntitle: User\n---\n# User\n",
        encoding="utf-8",
    )

    project_plan = build_semantic_write_plan(
        tmp_path,
        MemoryWriteReq(
            action="write",
            kind="decision",
            subject="sample",
            content="Sample is the active focus this week.",
            scope="project",
        ),
    )
    core_plan = build_semantic_write_plan(
        tmp_path,
        MemoryWriteReq(
            action="replace",
            kind="state",
            subject="user",
            content="Current memory defaults updated.",
            scope="core",
        ),
    )

    assert project_plan.subject_ref == "project:sample"
    assert project_plan.target_path == "decisions/sample.md"
    assert project_plan.resolved_mode == "append"
    assert project_plan.target_subject_ref == "decision:sample"
    assert core_plan.subject_ref == "core:user"
    assert core_plan.target_path == "core/user.md"
    assert core_plan.resolved_mode == "replace"


def test_build_semantic_write_plan_creates_new_project_from_explicit_scope(tmp_path: Path) -> None:
    plan = build_semantic_write_plan(
        tmp_path,
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="Open Privacy Filter",
            content="Open Privacy Filter is active.",
            scope="project",
            project="open-privacy-filter",
        ),
    )

    assert plan.subject_ref == "project:open-privacy-filter"
    assert plan.target_subject_ref == "project:open-privacy-filter"
    assert plan.target_path == "projects/open-privacy-filter/state.md"
    assert plan.matched_by == "explicit_scope"
    assert plan.target_exists is False


def test_build_semantic_write_plan_uses_cwd_for_current_project_subject(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace" / "mies"
    workspace.mkdir(parents=True)
    (tmp_path / "projects" / "palace").mkdir(parents=True)
    (tmp_path / "projects" / "palace" / "state.md").write_text(
        """---
title: Palace
type: project
workspace_aliases:
- mies
---
# Palace
""",
        encoding="utf-8",
    )

    plan = build_semantic_write_plan(
        tmp_path,
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="this project",
            content="Palace has canonical context from the current workspace.",
            scope="project",
            cwd=str(workspace),
        ),
    )

    assert plan.subject_ref == "project:palace"
    assert plan.target_subject_ref == "project:palace"
    assert plan.target_path == "projects/palace/state.md"
    assert plan.matched_by == "project_cwd"


def test_build_semantic_write_plan_keeps_personal_scope_from_cwd_project_bleed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace" / "mies"
    workspace.mkdir(parents=True)
    (tmp_path / "projects" / "palace").mkdir(parents=True)
    (tmp_path / "projects" / "palace" / "state.md").write_text(
        "---\ntitle: Palace\nworkspace_aliases:\n- mies\n---\n# Palace\n",
        encoding="utf-8",
    )
    (tmp_path / "people").mkdir(parents=True)
    (tmp_path / "people" / "alex.md").write_text(
        "---\ntitle: Alex\naliases:\n- user\n---\n# Alex\n",
        encoding="utf-8",
    )

    plan = build_semantic_write_plan(
        tmp_path,
        MemoryWriteReq(
            action="write",
            kind="preference",
            subject="user",
            content="User prefers concise project handoffs.",
            scope="person",
            cwd=str(workspace),
        ),
    )

    assert plan.subject_ref == "person:alex"
    assert plan.target_path == "people/alex.md"
    assert plan.family == "person"


def test_build_semantic_write_plan_does_not_create_people_from_explicit_scope(tmp_path: Path) -> None:
    try:
        build_semantic_write_plan(
            tmp_path,
            MemoryWriteReq(
                action="write",
                kind="preference",
                subject="active",
                content="Bad scoped proposal should not create a person.",
                scope="person",
            ),
        )
    except ValueError as err:
        assert "could not resolve semantic subject: active" in str(err)
    else:
        raise AssertionError("expected unresolved person subject to be rejected")


def test_build_semantic_write_plan_creates_new_dream_project_over_alias_match(tmp_path: Path) -> None:
    class _AliasResolver:
        def resolve(self, subject: str, *, scope: str | None = None) -> SubjectMatch | None:
            return SubjectMatch(
                subject_ref="project:privacy-filter-lab",
                family="project",
                title="Privacy Filter Lab",
                target_path="projects/privacy-filter-lab/state.md",
                matched_by="alias",
                confidence="high",
            )

    plan = build_semantic_write_plan(
        tmp_path,
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="open-privacy-filter",
            content="Open Privacy Filter is active.",
            scope="project",
            source="/var/lib/dory/digests/daily/2026-04-23.md",
        ),
        resolver=_AliasResolver(),
    )

    assert plan.subject_ref == "project:open-privacy-filter"
    assert plan.target_path == "projects/open-privacy-filter/state.md"


def test_build_semantic_write_plan_keeps_alias_match_for_non_dream_write(tmp_path: Path) -> None:
    class _AliasResolver:
        def resolve(self, subject: str, *, scope: str | None = None) -> SubjectMatch | None:
            return SubjectMatch(
                subject_ref="project:privacy-filter-lab",
                family="project",
                title="Privacy Filter Lab",
                target_path="projects/privacy-filter-lab/state.md",
                matched_by="alias",
                confidence="high",
            )

    plan = build_semantic_write_plan(
        tmp_path,
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="open-privacy-filter",
            content="Open Privacy Filter is active.",
            scope="project",
        ),
        resolver=_AliasResolver(),
    )

    assert plan.subject_ref == "project:privacy-filter-lab"
    assert plan.target_path == "projects/privacy-filter-lab/state.md"


def test_build_semantic_write_plan_demotes_unstated_new_project(tmp_path: Path) -> None:
    """Subject-only unknown projects must not mint canonical state pages."""
    plan = build_semantic_write_plan(
        tmp_path,
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="atlas companion audio debugging resume point",
            content="Session checkpoint, not a project.",
            scope="project",
        ),
    )

    assert plan.matched_by == "explicit_scope_new_project"
    assert plan.match_confidence == "low"


def test_semantic_write_quarantines_unstated_new_project(tmp_path: Path) -> None:
    engine = SemanticWriteEngine(tmp_path, resolver_client=None)

    resp = engine.write(
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="atlas companion audio debugging resume point",
            content="Session checkpoint, not a project.",
            scope="project",
            allow_canonical=True,
            soft=True,
        )
    )

    assert resp.result == "quarantined"
    assert not (tmp_path / "projects").exists()
