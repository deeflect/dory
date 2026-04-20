from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dory_core.llm.openrouter import OpenRouterProviderError
from dory_core.migration_batching import Batch, BatchFile
from dory_core.migration_entity_discovery import (
    _coerce_batch_entity,
    _coerce_canonical_entity,
    _fallback_canonicalize,
    _normalize_slug,
    _parse_canonical_entities,
    _parse_map_entities,
    discover_entities,
    write_entities,
)


@dataclass
class _FakeClient:
    map_payloads: list[Any] = field(default_factory=list)
    reduce_payload: Any = None
    reduce_error: bool = False
    map_errors: list[bool] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict,
    ) -> Any:
        self.calls.append(schema_name)
        if schema_name == "dory_entity_discovery_batch":
            if self.map_errors and self.map_errors.pop(0):
                raise OpenRouterProviderError("boom")
            return self.map_payloads.pop(0)
        if schema_name == "dory_entity_discovery_canonical":
            if self.reduce_error:
                raise OpenRouterProviderError("boom")
            return self.reduce_payload
        raise AssertionError(f"unexpected schema_name: {schema_name}")


def _batch(corpus: Path, label: str, files: list[str]) -> Batch:
    batch_files = []
    for rel in files:
        path = corpus / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("body\n", encoding="utf-8")
        batch_files.append(BatchFile(relative_path=Path(rel), token_count=100))
    return Batch(label=label, files=tuple(batch_files), token_total=len(files) * 100)


def test_normalize_slug_lowercases_and_dashes() -> None:
    assert _normalize_slug("  Clawsy_App  ") == "clawsy-app"
    assert _normalize_slug("AI Bubble") == "ai-bubble"
    assert _normalize_slug("project--with--doubles") == "project-with-doubles"


def test_coerce_batch_entity_rejects_missing_fields() -> None:
    assert _coerce_batch_entity({"slug": "x"}, batch_label="b") is None
    valid = {
        "slug": "Clawsy",
        "family": "project",
        "aliases": ["clawsy", "Clawzy"],
        "one_liner": "AI hosting SaaS",
        "status_signal": "active",
        "evidence_paths": ["projects/clawsy/state.md"],
        "mention_count": 3,
    }
    entity = _coerce_batch_entity(valid, batch_label="projects#1")
    assert entity is not None
    assert entity.slug == "clawsy"
    assert entity.batch_label == "projects#1"
    assert "clawsy" in entity.aliases


def test_coerce_canonical_entity_normalizes_slug_and_dedupes_aliases() -> None:
    valid = {
        "canonical_slug": "Clawsy",
        "family": "project",
        "aliases": ["clawsy", "Clawsy", "  clawsy  "],
        "one_liner": "AI hosting SaaS",
        "status_signal": "active",
        "evidence_paths": ["projects/clawsy/state.md", "projects/clawsy/state.md"],
        "mention_count": 5,
    }
    entity = _coerce_canonical_entity(valid)
    assert entity is not None
    assert entity.slug == "clawsy"
    assert entity.aliases == ("Clawsy", "clawsy")  # sorted + dedup


def test_parse_map_entities_drops_invalid_rows() -> None:
    payload = {
        "entities": [
            {
                "slug": "good",
                "family": "project",
                "aliases": [],
                "one_liner": "ok",
                "status_signal": "active",
                "evidence_paths": ["a.md"],
                "mention_count": 1,
            },
            {
                "slug": "bad-family",
                "family": "tool",
                "aliases": [],
                "one_liner": "ok",
                "status_signal": "active",
                "evidence_paths": ["a.md"],
                "mention_count": 1,
            },
        ]
    }
    entities = _parse_map_entities(payload, batch_label="x")
    assert len(entities) == 1
    assert entities[0].slug == "good"


def test_fallback_canonicalize_merges_by_exact_slug() -> None:
    from dory_core.migration_entity_discovery import BatchEntity

    raw = [
        BatchEntity(
            slug="clawsy",
            family="project",
            aliases=("Clawzy",),
            one_liner="AI hosting",
            status_signal="active",
            evidence_paths=("projects/clawsy/state.md",),
            mention_count=2,
            batch_label="a",
        ),
        BatchEntity(
            slug="clawsy",
            family="project",
            aliases=("Clawsy",),
            one_liner="AI hosting",
            status_signal="active",
            evidence_paths=("logs/daily/2026-04-01.md",),
            mention_count=3,
            batch_label="b",
        ),
    ]
    canonical = _fallback_canonicalize(raw)
    assert len(canonical) == 1
    merged = canonical[0]
    assert merged.mention_count == 5
    assert set(merged.aliases) == {"Clawzy", "Clawsy"}
    assert set(merged.evidence_paths) == {
        "projects/clawsy/state.md",
        "logs/daily/2026-04-01.md",
    }


def test_discover_entities_runs_map_then_reduce(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    batch = _batch(corpus, "projects#1", ["projects/clawsy/state.md"])
    client = _FakeClient(
        map_payloads=[{
            "entities": [{
                "slug": "clawsy",
                "family": "project",
                "aliases": ["Clawsy"],
                "one_liner": "AI hosting",
                "status_signal": "active",
                "evidence_paths": ["projects/clawsy/state.md"],
                "mention_count": 1,
            }]
        }],
        reduce_payload={
            "entities": [{
                "canonical_slug": "clawsy",
                "family": "project",
                "aliases": ["Clawsy", "clawsy"],
                "one_liner": "AI hosting SaaS",
                "status_signal": "active",
                "evidence_paths": ["projects/clawsy/state.md"],
                "mention_count": 1,
            }]
        },
    )

    report = discover_entities(corpus, [batch], client=client)  # type: ignore[arg-type]

    assert report.batches_processed == 1
    assert report.raw_entity_count == 1
    assert len(report.canonical_entities) == 1
    assert client.calls == [
        "dory_entity_discovery_batch",
        "dory_entity_discovery_canonical",
    ]


def test_discover_entities_falls_back_on_reduce_error(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    batch = _batch(corpus, "ideas#1", ["ideas/a.md"])
    client = _FakeClient(
        map_payloads=[{
            "entities": [{
                "slug": "ai-bubble",
                "family": "concept",
                "aliases": [],
                "one_liner": "viz concept",
                "status_signal": "active",
                "evidence_paths": ["ideas/a.md"],
                "mention_count": 1,
            }]
        }],
        reduce_error=True,
    )

    report = discover_entities(corpus, [batch], client=client)  # type: ignore[arg-type]

    assert len(report.canonical_entities) == 1
    assert report.canonical_entities[0].slug == "ai-bubble"


def test_discover_entities_counts_failed_batches(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    b1 = _batch(corpus, "batch#1", ["a.md"])
    b2 = _batch(corpus, "batch#2", ["b.md"])
    client = _FakeClient(
        map_payloads=[{
            "entities": [{
                "slug": "keep",
                "family": "project",
                "aliases": [],
                "one_liner": "ok",
                "status_signal": "active",
                "evidence_paths": ["a.md"],
                "mention_count": 1,
            }]
        }],
        map_errors=[False, True],
        reduce_payload={"entities": []},
    )

    report = discover_entities(corpus, [b1, b2], client=client)  # type: ignore[arg-type]

    assert report.batches_processed == 1
    assert report.batches_failed == 1


def test_write_entities_produces_valid_json(tmp_path: Path) -> None:
    from dory_core.migration_entity_discovery import CanonicalEntity, DiscoveryReport

    report = DiscoveryReport(
        batch_count=1,
        batches_processed=1,
        batches_failed=0,
        raw_entity_count=1,
        canonical_entities=[
            CanonicalEntity(
                slug="clawsy",
                family="project",
                aliases=("clawsy", "Clawzy"),
                one_liner="AI hosting",
                status_signal="active",
                evidence_paths=("projects/clawsy/state.md",),
                mention_count=3,
            )
        ],
    )
    target = tmp_path / "entities.json"
    write_entities(target, report)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["entities"][0]["slug"] == "clawsy"
    assert loaded["batches_processed"] == 1
