from __future__ import annotations


from dory_core.compiler import (
    COMPILER_INBOX_PREFIXES,
    RECALL_SOURCE_PREFIXES,
    CompilerArtifact,
    CompilerPipeline,
    assert_safe_to_ingest,
    context_fence_for_ingest,
    get_pipeline,
    is_compiler_artifact,
    is_inbox_path,
    is_recall_promotion_artifact,
    list_compiler_pipelines,
    llm_free_pipelines,
    pipelines_by_input_kind,
    pipelines_by_output_kind,
    schedulable_pipelines,
)


def test_list_compiler_pipelines_returns_inventory() -> None:
    pipelines = list_compiler_pipelines()
    assert isinstance(pipelines, dict)
    assert len(pipelines) >= 10
    assert "session_distillation" in pipelines
    assert "proposal_generation" in pipelines
    assert "daily_digest" in pipelines
    assert "weekly_digest" in pipelines
    assert "wiki_refresh" in pipelines
    assert "wiki_index_refresh" in pipelines
    assert "wiki_health" in pipelines
    assert "maintenance_report" in pipelines
    assert "recall_promotion" in pipelines
    assert "session_ingest" in pipelines
    assert "proposal_apply" in pipelines


def test_get_pipeline_returns_none_for_unknown() -> None:
    assert get_pipeline("nonexistent") is None


def test_pipeline_has_required_fields() -> None:
    pipelines = list_compiler_pipelines()
    for name, pipeline in pipelines.items():
        assert pipeline.name == name
        assert isinstance(pipeline.input_kinds, tuple)
        assert isinstance(pipeline.output_kind, str)
        assert isinstance(pipeline.output_is_reviewable, bool)
        assert isinstance(pipeline.can_promote, bool)
        assert isinstance(pipeline.uses_llm, bool)
        assert isinstance(pipeline.is_schedulable, bool)
        assert isinstance(pipeline.description, str)
        assert len(pipeline.description) > 0


def test_pipelines_by_output_kind() -> None:
    distilled = pipelines_by_output_kind("distilled")
    assert any(p.name == "session_distillation" for p in distilled)
    assert any(p.name == "recall_promotion" for p in distilled)

    proposals = pipelines_by_output_kind("proposal")
    assert any(p.name == "proposal_generation" for p in proposals)


def test_pipelines_by_input_kind() -> None:
    session_inputs = pipelines_by_input_kind("raw_session")
    assert any(p.name == "session_distillation" for p in session_inputs)
    assert any(p.name == "session_ingest" for p in session_inputs)

    recall_inputs = pipelines_by_input_kind("recall_event")
    assert any(p.name == "recall_promotion" for p in recall_inputs)


def test_schedulable_pipelines_includes_most() -> None:
    schedulable = schedulable_pipelines()
    names = {p.name for p in schedulable}
    assert "session_distillation" in names
    assert "proposal_generation" in names
    assert "wiki_refresh" in names
    assert "proposal_apply" not in names


def test_llm_free_pipelines_are_deterministic() -> None:
    free = llm_free_pipelines()
    names = {p.name for p in free}
    assert "wiki_refresh" in names
    assert "wiki_index_refresh" in names
    assert "wiki_health" in names
    assert "recall_promotion" in names
    assert "session_ingest" in names
    assert "proposal_apply" in names
    # LLM-dependent should NOT be in this list
    assert "session_distillation" not in names
    assert "proposal_generation" not in names
    assert "daily_digest" not in names
    assert "weekly_digest" not in names
    assert "maintenance_report" not in names


# ── Context Fencing ────────────────────────────────────────────────────


def test_is_compiler_artifact_detects_compiler_paths() -> None:
    assert is_compiler_artifact("wiki/people/avery.md") is True
    assert is_compiler_artifact("inbox/distilled/codex-session.md") is True
    assert is_compiler_artifact("inbox/proposed/proposal.json") is True
    assert is_compiler_artifact("inbox/applied/proposal.json") is True
    assert is_compiler_artifact("inbox/rejected/proposal.json") is True
    assert is_compiler_artifact("inbox/maintenance/report.json") is True
    assert is_compiler_artifact("inbox/semantic/note.md") is True
    assert is_compiler_artifact("digests/daily/2026-04-11.md") is True
    assert is_compiler_artifact("digests/weekly/2026-W17.md") is True
    assert is_compiler_artifact("eval/runs/2026-04-11/summary.md") is True


def test_is_compiler_artifact_returns_false_for_primary_sources() -> None:
    assert is_compiler_artifact("people/avery.md") is False
    assert is_compiler_artifact("core/active.md") is False
    assert is_compiler_artifact("logs/sessions/codex/2026-04-11.md") is False
    assert is_compiler_artifact("projects/dory/state.md") is False
    assert is_compiler_artifact("decisions/xyz.md") is False


def test_is_recall_promotion_artifact() -> None:
    assert is_recall_promotion_artifact("inbox/distilled/recall-people-avery.md") is True
    assert is_recall_promotion_artifact("inbox/distilled/recall-projects-dory.md") is True
    assert is_recall_promotion_artifact("inbox/distilled/codex-2026-04-11.md") is False
    assert is_recall_promotion_artifact("inbox/proposed/recall-people-avery.json") is False
    assert is_recall_promotion_artifact("people/avery.md") is False


def test_is_inbox_path() -> None:
    assert is_inbox_path("inbox/distilled/note.md") is True
    assert is_inbox_path("inbox/proposed/proposal.json") is True
    assert is_inbox_path("inbox/applied/proposal.json") is True
    assert is_inbox_path("inbox/rejected/proposal.json") is True
    assert is_inbox_path("inbox/maintenance/report.json") is True
    assert is_inbox_path("inbox/semantic/note.md") is True
    assert is_inbox_path("people/avery.md") is False
    assert is_inbox_path("wiki/people/avery.md") is False


def test_context_fence_for_ingest_fences_recall_promotion() -> None:
    warning = context_fence_for_ingest("inbox/distilled/recall-people-avery.md")
    assert warning is not None
    assert "recall promotion artifact" in warning
    assert "feedback loop" in warning


def test_context_fence_for_ingest_fences_inbox_paths() -> None:
    for inbox_path in ("inbox/distilled/codex-session.md", "inbox/proposed/proposal.json"):
        warning = context_fence_for_ingest(inbox_path)
        assert warning is not None
        assert "compiler artifact" in warning


def test_context_fence_for_ingest_fences_wiki_paths() -> None:
    warning = context_fence_for_ingest("wiki/people/avery.md")
    assert warning is not None
    assert "generated wiki page" in warning


def test_context_fence_for_ingest_fences_digest_paths() -> None:
    warning = context_fence_for_ingest("digests/daily/2026-04-11.md")
    assert warning is not None
    assert "digest page" in warning


def test_context_fence_for_ingest_allows_primary_sources() -> None:
    assert context_fence_for_ingest("people/avery.md") is None
    assert context_fence_for_ingest("core/active.md") is None
    assert context_fence_for_ingest("logs/sessions/codex/2026-04-11.md") is None
    assert context_fence_for_ingest("projects/dory/state.md") is None
    assert context_fence_for_ingest("decisions/xyz.md") is None
    assert context_fence_for_ingest("concepts/memory.md") is None


def test_assert_safe_to_ingest_raises_for_compiler_artifact() -> None:
    # Test actual paths that match the compiler output prefix checks
    unsafe_paths = (
        "inbox/distilled/codex-session.md",
        "inbox/proposed/proposal.json",
        "inbox/applied/proposal.json",
        "inbox/rejected/proposal.json",
        "inbox/maintenance/report.json",
        "inbox/semantic/note.md",
        "wiki/people/avery.md",
        "digests/daily/2026-04-11.md",
        "digests/weekly/2026-W17.md",
    )
    for path in unsafe_paths:
        try:
            assert_safe_to_ingest(path)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {path}")


def test_assert_safe_to_ingest_passes_for_primary_source() -> None:
    # Should not raise
    assert_safe_to_ingest("people/avery.md")
    assert_safe_to_ingest("core/active.md")


# ── CompilerArtifact ────────────────────────────────────────────────────


def test_compiler_artifact_has_required_fields() -> None:
    artifact = CompilerArtifact(
        kind="proposal",
        source_paths=("inbox/distilled/codex-session.md",),
        artifact_path="inbox/proposed/codex-session.json",
        producer="proposal_generation",
    )
    assert artifact.kind == "proposal"
    assert artifact.source_paths == ("inbox/distilled/codex-session.md",)
    assert artifact.artifact_path == "inbox/proposed/codex-session.json"
    assert artifact.producer == "proposal_generation"
    assert artifact.status == "pending"
    assert artifact.pipeline_name is None


# ── CompilerPipeline ────────────────────────────────────────────────────


def test_compiler_pipeline_is_frozen() -> None:
    pipeline = CompilerPipeline(
        name="test",
        input_kinds=("a", "b"),
        output_kind="c",
        output_is_reviewable=True,
        can_promote=False,
        uses_llm=False,
        is_schedulable=True,
        description="A test pipeline.",
    )
    assert pipeline.name == "test"
    assert pipeline.input_kinds == ("a", "b")
    assert pipeline.output_kind == "c"
    assert pipeline.output_is_reviewable is True
    assert pipeline.can_promote is False
    assert pipeline.uses_llm is False
    assert pipeline.is_schedulable is True
    assert pipeline.runner_class is None
    assert pipeline.cli_command is None


def test_compiler_pipeline_with_optional_fields() -> None:
    pipeline = CompilerPipeline(
        name="test_full",
        input_kinds=("a",),
        output_kind="b",
        output_is_reviewable=False,
        can_promote=True,
        uses_llm=True,
        is_schedulable=False,
        description="Full test pipeline.",
        runner_class="dory_core.test.Runner",
        cli_command="test run",
    )
    assert pipeline.runner_class == "dory_core.test.Runner"
    assert pipeline.cli_command == "test run"


# ── COMPILER_INBOX_PREFIXES ─────────────────────────────────────────────


def test_all_inbox_prefixes_are_in_compiler_output_prefixes() -> None:
    from dory_core.compiler import COMPILER_OUTPUT_PREFIXES

    for prefix in COMPILER_INBOX_PREFIXES:
        # Each inbox subdirectory is covered by the broader "inbox/" prefix
        assert any(
            prefix == op or prefix.startswith(op) for op in COMPILER_OUTPUT_PREFIXES
        ), f"{prefix} not covered by {COMPILER_OUTPUT_PREFIXES}"


def test_recall_source_prefixes_are_in_inbox_distilled() -> None:
    for prefix in RECALL_SOURCE_PREFIXES:
        assert prefix in COMPILER_INBOX_PREFIXES or prefix.startswith("inbox/distilled/")
