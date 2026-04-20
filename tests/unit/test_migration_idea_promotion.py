from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dory_core.frontmatter import load_markdown_document
from dory_core.llm.openrouter import OpenRouterProviderError
from dory_core.migration_entity_discovery import CanonicalEntity
from dory_core.migration_idea_promotion import (
    _coerce_decision,
    promote_ideas,
)


@dataclass
class _FakeClient:
    payloads: list[Any] = field(default_factory=list)
    errors: list[bool] = field(default_factory=list)

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict,
    ) -> Any:
        if self.errors and self.errors.pop(0):
            raise OpenRouterProviderError("boom")
        return self.payloads.pop(0)


def _write_idea(corpus: Path, stem: str, body: str = "idea body\n") -> Path:
    path = corpus / "ideas" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {stem}\ntype: idea\nstatus: pending\n---\n\n{body}",
        encoding="utf-8",
    )
    return path


def _entity(slug: str, family: str = "project") -> CanonicalEntity:
    return CanonicalEntity(
        slug=slug,
        family=family,  # type: ignore[arg-type]
        aliases=(),
        one_liner=f"{slug} one-liner",
        status_signal="active",
        evidence_paths=(),
        mention_count=1,
    )


def test_coerce_decision_rejects_invalid_rows() -> None:
    assert _coerce_decision({"source_path": ""}) is None
    assert (
        _coerce_decision(
            {
                "source_path": "ideas/a.md",
                "classification": "bogus",
                "target_slug": None,
                "rationale": "x",
            }
        )
        is None
    )
    good = _coerce_decision(
        {
            "source_path": "ideas/a.md",
            "classification": "stay",
            "target_slug": None,
            "rationale": "x",
        }
    )
    assert good is not None
    assert good.classification == "stay"


def test_promote_to_project_moves_idea_file(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_idea(corpus, "2026-02-19-casey-ceo", "spec for casey-ceo site with domain\n")
    client = _FakeClient(
        payloads=[
            {
                "decisions": [
                    {
                        "source_path": "ideas/2026-02-19-casey-ceo.md",
                        "classification": "promote_to_project",
                        "target_slug": "casey-ceo",
                        "rationale": "has spec and domain",
                    }
                ]
            }
        ]
    )

    report = promote_ideas(corpus, [], client=client)  # type: ignore[arg-type]

    assert report.promoted_to_project == 1
    assert (corpus / "projects" / "casey-ceo" / "state.md").exists()
    assert not (corpus / "ideas" / "2026-02-19-casey-ceo.md").exists()
    doc = load_markdown_document((corpus / "projects" / "casey-ceo" / "state.md").read_text(encoding="utf-8"))
    assert doc.frontmatter["type"] == "project"
    assert doc.frontmatter["canonical"] is True


def test_promote_to_concept_moves_to_concepts_bucket(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_idea(corpus, "2026-02-19-fly-mode", "working style pattern")
    client = _FakeClient(
        payloads=[
            {
                "decisions": [
                    {
                        "source_path": "ideas/2026-02-19-fly-mode.md",
                        "classification": "promote_to_concept",
                        "target_slug": "fly-mode",
                        "rationale": "durable mental model",
                    }
                ]
            }
        ]
    )

    report = promote_ideas(corpus, [], client=client)  # type: ignore[arg-type]

    assert report.promoted_to_concept == 1
    assert (corpus / "concepts" / "fly-mode.md").exists()
    doc = load_markdown_document((corpus / "concepts" / "fly-mode.md").read_text(encoding="utf-8"))
    assert doc.frontmatter["type"] == "concept"


def test_stay_keeps_idea_in_place(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_idea(corpus, "unformed")
    client = _FakeClient(
        payloads=[
            {
                "decisions": [
                    {
                        "source_path": "ideas/unformed.md",
                        "classification": "stay",
                        "target_slug": None,
                        "rationale": "too raw",
                    }
                ]
            }
        ]
    )

    report = promote_ideas(corpus, [], client=client)  # type: ignore[arg-type]

    assert report.stayed == 1
    assert (corpus / "ideas" / "unformed.md").exists()


def test_merge_with_entity_appends_backlink(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_idea(corpus, "clawsy-pricing-thoughts", "just notes about clawsy pricing\n")
    client = _FakeClient(
        payloads=[
            {
                "decisions": [
                    {
                        "source_path": "ideas/clawsy-pricing-thoughts.md",
                        "classification": "merge_with_entity",
                        "target_slug": "clawsy",
                        "rationale": "direct pricing evidence for clawsy",
                    }
                ]
            }
        ]
    )

    report = promote_ideas(corpus, [_entity("clawsy")], client=client)  # type: ignore[arg-type]

    assert report.merged_into_entity == 1
    text = (corpus / "ideas" / "clawsy-pricing-thoughts.md").read_text(encoding="utf-8")
    assert "<!-- merged-with: clawsy -->" in text


def test_dry_run_leaves_files_in_place(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_idea(corpus, "casey-ceo-spec")
    client = _FakeClient(
        payloads=[
            {
                "decisions": [
                    {
                        "source_path": "ideas/casey-ceo-spec.md",
                        "classification": "promote_to_project",
                        "target_slug": "casey-ceo",
                        "rationale": "spec-heavy",
                    }
                ]
            }
        ]
    )

    report = promote_ideas(corpus, [], client=client, dry_run=True)  # type: ignore[arg-type]

    assert report.promoted_to_project == 1
    assert (corpus / "ideas" / "casey-ceo-spec.md").exists()
    assert not (corpus / "projects" / "casey-ceo" / "state.md").exists()


def test_llm_error_skips_entire_batch(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write_idea(corpus, "a")
    _write_idea(corpus, "b")
    client = _FakeClient(errors=[True])

    report = promote_ideas(corpus, [], client=client)  # type: ignore[arg-type]

    assert report.skipped == 2
    assert (corpus / "ideas" / "a.md").exists()
    assert (corpus / "ideas" / "b.md").exists()


def test_empty_ideas_directory_returns_zero_report(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    (corpus / "ideas").mkdir(parents=True)
    client = _FakeClient()

    report = promote_ideas(corpus, [], client=client)  # type: ignore[arg-type]

    assert report.total_ideas == 0
    assert report.promoted_to_concept == 0
    assert report.promoted_to_project == 0
