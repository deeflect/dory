from __future__ import annotations

from pathlib import Path

from dory_core.semantic_write import SubjectResolver, build_semantic_write_plan
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
