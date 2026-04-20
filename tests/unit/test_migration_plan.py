from __future__ import annotations

from math import isclose
from pathlib import Path

from dory_core.config import DorySettings
from dory_core.llm.openrouter import resolve_openrouter_model_metadata
from dory_core.migration_plan import MigrationPlanner, MigrationScope


class StableTokenCounter:
    def count(self, text: str, *, agent: str = "default") -> int:
        del agent
        return 1 if text.strip() else 0


def test_scan_corpus_groups_top_level_buckets(tmp_path: Path) -> None:
    _write_markdown(tmp_path / "USER.md", "# User\n\nRoot user profile.")
    _write_markdown(tmp_path / "SOUL.md", "# Soul\n\nRoot soul prompt.")
    _write_markdown(tmp_path / "memory" / "daily" / "2026-03-25-digest.md", "# Daily\n\nDigest body.")
    _write_markdown(tmp_path / "memory" / "sessions" / "2026-03-20-revenue-plan.md", "# Session\n\nSession body.")
    _write_markdown(tmp_path / "projects" / "rooster.md", "# Rooster\n\nProject body.")
    _write_markdown(tmp_path / "concepts" / "openclaw.md", "# OpenClaw\n\nConcept body.")

    planner = MigrationPlanner(token_counter=StableTokenCounter())
    scan = planner.scan_corpus(tmp_path)

    assert scan.markdown_count == 6
    assert scan.byte_count > 0
    assert [stat.folder for stat in scan.folder_stats] == ["concepts", "memory", "projects", "root"]
    assert {stat.folder: stat.markdown_count for stat in scan.folder_stats} == {
        "concepts": 1,
        "memory": 2,
        "projects": 1,
        "root": 2,
    }


def test_build_plan_supports_folder_scope_and_even_sampling(tmp_path: Path) -> None:
    _write_markdown(tmp_path / "USER.md", "# User\n\nRoot user profile.")
    _write_markdown(tmp_path / "memory" / "daily" / "2026-03-25-digest.md", "# Daily\n\nDigest one.")
    _write_markdown(tmp_path / "memory" / "sessions" / "2026-03-20-revenue-plan.md", "# Session\n\nSession one.")
    _write_markdown(tmp_path / "memory" / "weekly" / "2026-W12.md", "# Weekly\n\nWeekly rollup.")
    _write_markdown(tmp_path / "projects" / "alpha.md", "# Alpha\n\nProject alpha.")
    _write_markdown(tmp_path / "projects" / "beta.md", "# Beta\n\nProject beta.")
    _write_markdown(tmp_path / "concepts" / "openclaw.md", "# OpenClaw\n\nConcept body.")

    planner = MigrationPlanner(token_counter=StableTokenCounter(), preview_limit=3)
    scan = planner.scan_corpus(tmp_path)
    plan = planner.build_plan(scan, scope=MigrationScope(selected_roots=("memory", "projects"), sample_size=2))

    assert plan.scope.selection_mode == "sample"
    assert plan.selected_markdown_count == 2
    assert [path.relative_to(tmp_path).as_posix() for path in plan.selected_markdown_files] == [
        "memory/daily/2026-03-25-digest.md",
        "projects/alpha.md",
    ]
    assert [path.relative_to(tmp_path).as_posix() for path in plan.preview_files] == [
        "memory/daily/2026-03-25-digest.md",
        "projects/alpha.md",
    ]


def test_build_plan_estimates_tokens_and_costs_from_openrouter_pricing_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_markdown(tmp_path / "memory" / "daily" / "2026-03-25-digest.md", "# Daily\n\nDigest one.")
    monkeypatch.setenv("DORY_OPENROUTER_INPUT_PRICE_PER_MILLION", "1.5")
    monkeypatch.setenv("DORY_OPENROUTER_OUTPUT_PRICE_PER_MILLION", "2.5")

    planner = MigrationPlanner(token_counter=StableTokenCounter())
    plan = planner.plan_corpus(tmp_path)

    assert plan.estimate.model == "google/gemini-3.1-flash-lite-preview"
    assert plan.estimate.pricing is not None
    assert plan.estimate.classification_input_tokens == 2
    assert plan.estimate.extraction_input_tokens == 0
    assert plan.estimate.estimated_input_tokens == 2
    assert plan.estimate.classification_output_tokens == 0
    assert plan.estimate.extraction_output_tokens == 300
    assert plan.estimate.estimated_output_tokens == 300
    assert plan.estimate.estimated_total_tokens == 302
    assert isclose(plan.estimate.estimated_input_usd or 0.0, 0.000003, rel_tol=0.0, abs_tol=1e-9)
    assert isclose(plan.estimate.estimated_output_usd or 0.0, 0.00075, rel_tol=0.0, abs_tol=1e-9)
    assert isclose(plan.estimate.estimated_total_usd or 0.0, 0.000753, rel_tol=0.0, abs_tol=1e-9)


def test_resolve_openrouter_model_metadata_uses_settings_and_pricing_env(monkeypatch) -> None:
    monkeypatch.setenv("DORY_OPENROUTER_INPUT_PRICE_PER_MILLION", "0.25")
    monkeypatch.setenv("DORY_OPENROUTER_OUTPUT_PRICE_PER_MILLION", "0.5")

    metadata = resolve_openrouter_model_metadata(
        DorySettings(
            openrouter_model="google/gemini-3.1-flash-lite-preview",
            openrouter_maintenance_model="google/gemini-2.5-flash-lite",
        ),
        purpose="maintenance",
    )

    assert metadata.model == "google/gemini-2.5-flash-lite"
    assert metadata.pricing is not None
    assert metadata.pricing.input_usd_per_million == 0.25
    assert metadata.pricing.output_usd_per_million == 0.5


def _write_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
