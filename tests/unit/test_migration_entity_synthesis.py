from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dory_core.frontmatter import load_markdown_document
from dory_core.llm.openrouter import OpenRouterProviderError
from dory_core.migration_entity_discovery import CanonicalEntity
from dory_core.migration_entity_synthesis import (
    load_entities_from_json,
    synthesize_entities,
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


def _entity(
    slug: str,
    family: str = "project",
    evidence_paths: tuple[str, ...] = (),
    aliases: tuple[str, ...] = (),
) -> CanonicalEntity:
    return CanonicalEntity(
        slug=slug,
        family=family,  # type: ignore[arg-type]
        aliases=aliases,
        one_liner=f"{slug} one-liner",
        status_signal="active",
        evidence_paths=evidence_paths,
        mention_count=max(1, len(evidence_paths)),
    )


def _write(path: Path, content: str = "body") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _synthesis_payload(
    *,
    title: str = "Clawsy",
    sections: list[dict] | None = None,
    timeline: list[dict] | None = None,
    aliases: list[str] | None = None,
    evidence: list[str] | None = None,
) -> dict:
    return {
        "title": title,
        "summary": "Active AI hosting SaaS.",
        "sections": sections
        or [
            {"heading": "Current State", "body": "Pricing tiers live."},
            {"heading": "Goals", "body": "Ship BYOK tier."},
        ],
        "timeline_entries": timeline
        or [{"date": "2026-04-10", "note": "Shipped landing", "evidence_path": "logs/daily/2026-04-10.md"}],
        "aliases": aliases or ["clawsy", "Clawzy"],
        "evidence_cited": evidence or ["projects/clawsy/state.md"],
    }


def test_synthesize_writes_canonical_page_with_timeline(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write(
        corpus / "projects" / "clawsy" / "state.md",
        "---\ntitle: clawsy\ntype: project\nstatus: active\n---\n\nraw\n",
    )

    client = _FakeClient(payloads=[_synthesis_payload()])
    entities = [
        _entity("clawsy", evidence_paths=("projects/clawsy/state.md",))
    ]

    report = synthesize_entities(entities, corpus_root=corpus, client=client)  # type: ignore[arg-type]

    assert report.synthesized == 1
    target = corpus / "projects" / "clawsy" / "state.md"
    text = target.read_text(encoding="utf-8")
    doc = load_markdown_document(text)
    assert doc.frontmatter["canonical"] is True
    assert doc.frontmatter["source_kind"] == "distilled"
    assert "## Current State" in doc.body
    assert "<!-- TIMELINE: append-only below this line -->" in doc.body
    assert "2026-04-10: Shipped landing" in doc.body


def test_synthesize_skips_entities_with_no_evidence(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    client = _FakeClient()
    entities = [_entity("ghost", evidence_paths=("projects/ghost/state.md",))]

    report = synthesize_entities(entities, corpus_root=corpus, client=client)  # type: ignore[arg-type]

    assert report.synthesized == 0
    assert report.skipped_no_evidence == 1


def test_synthesize_handles_llm_error_without_writing(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write(corpus / "projects" / "alpha" / "state.md", "body")
    client = _FakeClient(payloads=[], errors=[True])
    entities = [_entity("alpha", evidence_paths=("projects/alpha/state.md",))]

    report = synthesize_entities(entities, corpus_root=corpus, client=client)  # type: ignore[arg-type]

    assert report.synthesized == 0
    assert report.skipped_llm_error == 1
    # The stub source remains unchanged.
    assert (corpus / "projects" / "alpha" / "state.md").read_text(encoding="utf-8") == "body"


def test_synthesize_merges_active_and_archive_as_evidence(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write(corpus / "projects" / "borb-bot" / "state.md", "active content")
    _write(corpus / "archive" / "projects" / "borb-bot.md", "archived content")
    client = _FakeClient(
        payloads=[
            _synthesis_payload(
                title="borb-bot",
                evidence=["projects/borb-bot/state.md", "archive/projects/borb-bot.md"],
            )
        ]
    )
    entities = [
        _entity(
            "borb-bot",
            evidence_paths=("projects/borb-bot/state.md", "archive/projects/borb-bot.md"),
        )
    ]

    report = synthesize_entities(entities, corpus_root=corpus, client=client)  # type: ignore[arg-type]

    assert report.synthesized == 1
    doc = load_markdown_document(
        (corpus / "projects" / "borb-bot" / "state.md").read_text(encoding="utf-8")
    )
    assert "projects/borb-bot/state.md" in doc.body
    assert "archive/projects/borb-bot.md" in doc.body


def test_synthesize_person_family_lands_under_people(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _write(corpus / "people" / "casey.md", "source")
    client = _FakeClient(
        payloads=[
            _synthesis_payload(
                title="Casey",
                sections=[{"heading": "Current Facts", "body": "Based in LA."}],
                evidence=["people/casey.md"],
            )
        ]
    )
    entities = [_entity("casey", family="person", evidence_paths=("people/casey.md",))]

    report = synthesize_entities(entities, corpus_root=corpus, client=client)  # type: ignore[arg-type]

    assert report.synthesized == 1
    doc = load_markdown_document(
        (corpus / "people" / "casey.md").read_text(encoding="utf-8")
    )
    assert doc.frontmatter["type"] == "person"
    assert "## Current Facts" in doc.body


def test_load_entities_from_json_round_trip(tmp_path: Path) -> None:
    payload = {
        "entities": [
            {
                "slug": "clawsy",
                "family": "project",
                "aliases": ["clawsy"],
                "one_liner": "AI hosting",
                "status_signal": "active",
                "evidence_paths": ["projects/clawsy/state.md"],
                "mention_count": 3,
            }
        ]
    }
    target = tmp_path / "entities.json"
    target.write_text(json.dumps(payload), encoding="utf-8")

    entities = load_entities_from_json(target)
    assert len(entities) == 1
    assert entities[0].slug == "clawsy"
    assert entities[0].family == "project"
    assert entities[0].mention_count == 3
