from __future__ import annotations

from pathlib import Path

from dory_core.semantic_write import SemanticWriteEngine, SubjectMatch, SubjectResolver, build_semantic_write_plan
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


def test_semantic_write_reuses_existing_evidence_for_idempotent_replay(tmp_path: Path) -> None:
    engine = SemanticWriteEngine(tmp_path, resolver_client=None)
    req = MemoryWriteReq(
        action="write",
        kind="state",
        subject="Open Privacy Filter",
        content="Open Privacy Filter is active in characterization tests.",
        scope="project",
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
        ),
    )

    assert plan.subject_ref == "project:open-privacy-filter"
    assert plan.target_subject_ref == "project:open-privacy-filter"
    assert plan.target_path == "projects/open-privacy-filter/state.md"
    assert plan.matched_by == "explicit_scope"
    assert plan.target_exists is False


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
