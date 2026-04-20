from __future__ import annotations

import pytest

from dory_core.migration_llm import (
    ExtractionContext,
    MigrationLLM,
    parse_migration_audit_response,
    parse_migration_repair_response,
    parse_document_response,
    parse_entity_resolution_response,
    parse_classification_response,
    parse_extraction_response,
)
from dory_core.migration_prompts import (
    build_classification_system_prompt,
    build_classification_user_prompt,
    build_entity_resolution_system_prompt,
    build_entity_resolution_user_prompt,
    build_extraction_system_prompt,
    build_extraction_user_prompt,
    classification_schema_name,
    document_schema_name,
    entity_resolution_schema_name,
    extraction_schema_name,
    migration_audit_schema_name,
    migration_repair_schema_name,
)
from dory_core.migration_types import ClassifiedDocument, MigrationEntityMention


class FakeClient:
    def __init__(self, payload: dict[str, object] | list[dict[str, object]]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


def test_prompt_builders_are_specific() -> None:
    classification_prompt = build_classification_system_prompt()
    extraction_prompt = build_extraction_system_prompt()
    classified = ClassifiedDocument(
        doc_class="digest_daily",
        canonicality="evidence",
        target_path="digests/daily/2026-04-13.md",
        domain="mixed",
        entity_refs=("project:rooster",),
        decision_refs=(),
        time_scope="current",
        confidence="high",
        action="route_final",
        reason="daily digest",
    )

    assert "Return JSON only" in classification_prompt
    assert "canonicality" in classification_prompt
    assert "Split mixed digests and sessions" in extraction_prompt
    assert "typed memory atoms" in extraction_prompt
    assert build_classification_user_prompt(path="memory/daily.md", text="hello").startswith("Document path:")
    extraction_user_prompt = build_extraction_user_prompt(
        path="memory/daily.md",
        text="hello",
        classified=classified,
    )
    assert extraction_user_prompt.startswith("Document path:")
    assert "Known doc_class: digest_daily" in extraction_user_prompt
    assert "Use this evidence_path for every atom: digests/daily/2026-04-13.md" in extraction_user_prompt


def test_schema_names_are_stable() -> None:
    assert classification_schema_name() == "dory_migration_classification"
    assert extraction_schema_name() == "dory_migration_atoms"
    assert document_schema_name() == "dory_migration_document"
    assert entity_resolution_schema_name() == "dory_migration_entity_resolution"
    assert migration_audit_schema_name() == "dory_migration_audit"
    assert migration_repair_schema_name() == "dory_migration_repair"


def test_parse_classification_response() -> None:
    parsed = parse_classification_response(
        {
            "doc_class": "project_state",
            "canonicality": "canonical",
            "target_path": "projects/rooster/state.md",
            "domain": "work",
            "entity_refs": ["project:rooster"],
            "decision_refs": [],
            "time_scope": "current",
            "confidence": "high",
            "action": "route_final",
            "reason": "direct project page",
        }
    )

    assert parsed.doc_class == "project_state"
    assert parsed.target_path == "projects/rooster/state.md"
    assert parsed.entity_refs == ("project:rooster",)


def test_parse_classification_response_normalizes_path_style_refs() -> None:
    parsed = parse_classification_response(
        {
            "doc_class": "core_user",
            "canonicality": "canonical",
            "target_path": "core/user.md",
            "domain": "mixed",
            "entity_refs": ["people/casey", "projects/valk", "concepts/async-work"],
            "decision_refs": ["decisions/active-memory"],
            "time_scope": "current",
            "confidence": "high",
            "action": "route_final",
            "reason": "root profile",
        }
    )

    assert parsed.entity_refs == ("person:casey", "project:valk", "concept:async-work")
    assert parsed.decision_refs == ("decision:active-memory",)


def test_parse_extraction_response() -> None:
    atoms = parse_extraction_response(
        [
            {
                "kind": "project_update",
                "subject_ref": "project:rooster",
                "payload": {"summary": "registry first"},
                "evidence_path": "digests/daily/2026-04-13.md",
                "time_ref": "2026-04-13",
                "confidence": "high",
            },
            {
                "kind": "decision",
                "subject_ref": "decision:migration",
                "payload": {"summary": "use hybrid schema"},
                "evidence_path": "sessions/2026-04-13.md",
                "time_ref": None,
                "confidence": "medium",
            },
        ]
    )

    assert len(atoms) == 2
    assert atoms[0].kind == "project_update"
    assert atoms[1].time_ref is None


def test_parse_extraction_response_normalizes_subject_refs_and_rejects_empty_payloads() -> None:
    atoms = parse_extraction_response(
        [
            {
                "kind": "person_fact",
                "subject_ref": "people/casey",
                "payload": {"summary": "uses async work"},
                "evidence_path": "digests/daily/2026-04-13.md",
                "time_ref": "2026-04-13",
                "confidence": "high",
            }
        ]
    )

    assert atoms[0].subject_ref == "person:casey"

    with pytest.raises(ValueError):
        parse_extraction_response(
            [
                {
                    "kind": "person_fact",
                    "subject_ref": "people/casey",
                    "payload": {},
                    "evidence_path": "digests/daily/2026-04-13.md",
                    "time_ref": "2026-04-13",
                    "confidence": "high",
                }
            ]
        )


def test_parse_extraction_response_can_salvage_valid_atoms_with_context() -> None:
    atoms = parse_extraction_response(
        [
            {
                "kind": "project_update",
                "subject_ref": "rooster",
                "payload": {"summary": "Rooster is active again."},
                "evidence_path": "wrong/path.md",
                "time_ref": "2026-04-13",
                "confidence": "high",
            },
            {
                "kind": "person_fact",
                "subject_ref": "casey",
                "payload": {},
                "evidence_path": "wrong/path.md",
                "time_ref": "2026-04-13",
                "confidence": "high",
            },
        ],
        context=ExtractionContext(
            evidence_path="digests/daily/2026-04-13.md",
            entity_refs=("project:rooster",),
        ),
        strict=False,
    )

    assert len(atoms) == 1
    assert atoms[0].subject_ref == "project:rooster"
    assert atoms[0].evidence_path == "digests/daily/2026-04-13.md"


def test_parse_extraction_response_derives_summary_from_payload_fields() -> None:
    atoms = parse_extraction_response(
        [
            {
                "kind": "decision",
                "subject_ref": "decision:memory-core",
                "payload": {"decision": "Use Dory as the primary memory backend."},
                "evidence_path": "digests/daily/2026-04-13.md",
                "time_ref": "2026-04-13",
                "confidence": "high",
            }
        ]
    )

    assert atoms[0].payload["summary"] == "Use Dory as the primary memory backend."


def test_parse_document_response_keeps_resolution_boundary() -> None:
    extracted = parse_document_response(
        {
            "doc_class": "person_profile",
            "canonicality": "canonical",
            "target_path": "people/casey.md",
            "domain": "mixed",
            "entity_refs": ["person:casey"],
            "decision_refs": [],
            "time_scope": "current",
            "confidence": "high",
            "action": "route_final",
            "reason": "clear person profile",
            "source_quality": "strong",
            "resolution_mode": "resolved",
            "quarantine_reason": None,
            "entity_candidates": [
                {
                    "ref": "people/casey",
                    "display_name": "Casey",
                    "aliases": ["Jordan Example"],
                    "confidence": "high",
                }
            ],
            "atoms": [
                {
                    "kind": "person_fact",
                    "subject_ref": "person:casey",
                    "payload": {"summary": "Prefers async work."},
                    "evidence_path": "people/casey.md",
                    "time_ref": "2026-04-13",
                    "confidence": "high",
                }
            ],
        }
    )

    assert extracted.resolution_mode == "resolved"
    assert extracted.entity_candidates[0].ref == "person:casey"
    assert extracted.entity_candidates[0].aliases == ("Jordan Example",)
    assert extracted.atoms[0].subject_ref == "person:casey"


def test_migration_llm_calls_openrouter_with_structured_schemas() -> None:
    client = FakeClient(
        {
            "doc_class": "concept_note",
            "canonicality": "evidence",
            "target_path": "sources/legacy/memory/tools/openclaw.md",
            "domain": "mixed",
            "entity_refs": ["concept:openclaw"],
            "decision_refs": [],
            "time_scope": "timeless",
            "confidence": "high",
            "action": "store_as_source",
            "reason": "legacy tool note",
        }
    )
    engine = MigrationLLM(client=client)

    classified = engine.classify_document(path="memory/tools/openclaw.md", text="# OpenClaw")

    assert classified.doc_class == "concept_note"
    assert client.calls[0]["schema_name"] == "dory_migration_classification"
    assert "OpenClaw" in str(client.calls[0]["user_prompt"])
    assert "doc_class" in client.calls[0]["schema"]["properties"]


def test_migration_llm_extracts_document_with_resolution_mode() -> None:
    client = FakeClient(
        {
            "doc_class": "project_state",
            "canonicality": "evidence",
            "target_path": "projects/rooster/state.md",
            "domain": "work",
            "entity_refs": ["project:rooster"],
            "decision_refs": [],
            "time_scope": "current",
            "confidence": "high",
            "action": "merge_into_existing",
            "reason": "grounded project update",
            "source_quality": "strong",
            "resolution_mode": "resolved",
            "quarantine_reason": None,
            "entity_candidates": [
                {
                    "ref": "project:rooster",
                    "display_name": "Rooster",
                    "aliases": [],
                    "confidence": "high",
                }
            ],
            "atoms": [
                {
                    "kind": "project_update",
                    "subject_ref": "project:rooster",
                    "payload": {"summary": "Rooster is active again."},
                    "evidence_path": "projects/rooster/state.md",
                    "time_ref": "2026-04-13",
                    "confidence": "high",
                }
            ],
        }
    )
    engine = MigrationLLM(client=client)

    extracted = engine.extract_document(path="memory/projects/rooster.md", text="# Rooster")

    assert extracted.resolution_mode == "resolved"
    assert extracted.classified.target_path == "projects/rooster/state.md"
    assert client.calls[0]["schema_name"] == "dory_migration_document"
    assert "Required output:" in client.calls[0]["user_prompt"]


def test_migration_llm_extracts_atoms_and_handles_non_list_payloads() -> None:
    classified = ClassifiedDocument(
        doc_class="person_profile",
        canonicality="canonical",
        target_path="people/casey.md",
        domain="mixed",
        entity_refs=("person:casey",),
        decision_refs=(),
        time_scope="current",
        confidence="high",
        action="route_final",
        reason="profile note",
    )
    client = FakeClient(
        {
            "atoms": [
                {
                    "kind": "person_fact",
                    "subject_ref": "person:casey",
                    "payload": {"summary": "uses async work"},
                    "evidence_path": "digests/daily/2026-04-13.md",
                    "time_ref": "2026-04-13",
                    "confidence": "high",
                }
            ]
        }
    )
    engine = MigrationLLM(client=client)

    atoms = engine.extract_atoms(path="memory/people/casey.md", text="# Casey", classified=classified)

    assert len(atoms) == 1
    assert atoms[0].kind == "person_fact"
    assert client.calls[0]["schema_name"] == "dory_migration_atoms"
    assert "Use this evidence_path for every atom: people/casey.md" in client.calls[0]["user_prompt"]

    empty_engine = MigrationLLM(client=FakeClient({"unexpected": "shape"}))  # type: ignore[arg-type]
    assert empty_engine.extract_atoms(path="memory/daily.md", text="hi", classified=classified) == ()


def test_entity_resolution_prompt_and_parser() -> None:
    prompt = build_entity_resolution_system_prompt()
    user_prompt = build_entity_resolution_user_prompt(
        family="person",
        candidates=[
            {
                "key": "a",
                "ref": "person:casey",
                "family": "person",
                "display_name": "Casey",
                "aliases": ["Jordan Example"],
                "source_path": "notes/a.md",
            }
        ],
        existing_entities=[
            {
                "entity_id": "person:casey",
                "family": "person",
                "title": "Casey",
                "target_path": "people/casey.md",
                "aliases": ["jordan-example"],
            }
        ],
    )

    assert "Resolve migration entity candidates" in prompt
    assert "Candidate mentions" in user_prompt
    assert "Existing registry entities" in user_prompt

    clusters = parse_entity_resolution_response(
        {
            "clusters": [
                {
                    "canonical_ref": "person:casey",
                    "family": "person",
                    "display_name": "Casey",
                    "aliases": ["Jordan Example"],
                    "member_keys": ["a"],
                }
            ]
        },
        expected_family="person",
    )

    assert clusters[0].canonical_ref == "person:casey"
    assert clusters[0].member_keys == ("a",)


def test_migration_llm_resolves_entity_mentions() -> None:
    client = FakeClient(
        {
            "clusters": [
                {
                    "canonical_ref": "person:casey",
                    "family": "person",
                    "display_name": "Casey",
                    "aliases": ["Jordan Example"],
                    "member_keys": ["notes/a.md::0::person:casey", "notes/b.md::0::person:jordan-example"],
                }
            ]
        }
    )
    engine = MigrationLLM(client=client)

    clusters = engine.resolve_entity_mentions(
        family="person",
        candidates=(
            MigrationEntityMention(
                key="notes/a.md::0::person:casey",
                ref="person:casey",
                family="person",
                display_name="Casey",
                aliases=("Jordan Example",),
                source_path="notes/a.md",
            ),
            MigrationEntityMention(
                key="notes/b.md::0::person:jordan-example",
                ref="person:jordan-example",
                family="person",
                display_name="Jordan Example",
                aliases=("Casey",),
                source_path="notes/b.md",
            ),
        ),
        existing_entities=(),
    )

    assert len(clusters) == 1
    assert clusters[0].canonical_ref == "person:casey"
    assert client.calls[0]["schema_name"] == "dory_migration_entity_resolution"


def test_parse_and_run_migration_audit() -> None:
    audits = parse_migration_audit_response(
        {
            "audits": [
                {
                    "path": "people/casey.md",
                    "verdict": "review",
                    "summary": "Evidence is thin.",
                    "issues": ["single weak source"],
                }
            ]
        }
    )

    assert audits[0].path == "people/casey.md"
    assert audits[0].verdict == "review"

    client = FakeClient(
        {
            "audits": [
                {
                    "path": "people/casey.md",
                    "verdict": "pass",
                    "summary": "Looks grounded.",
                    "issues": [],
                }
            ]
        }
    )
    engine = MigrationLLM(client=client)
    result = engine.audit_migration_pages(pages=({"path": "people/casey.md", "content": "# Casey"},))

    assert result[0].verdict == "pass"
    assert client.calls[0]["schema_name"] == "dory_migration_audit"


def test_parse_and_run_migration_repair() -> None:
    repairs = parse_migration_repair_response(
        {
            "repairs": [
                {
                    "path": "people/casey.md",
                    "apply": True,
                    "summary": "Narrowed the page to grounded facts.",
                    "content": "---\ntitle: Casey\n---\n\n# Casey\n",
                }
            ]
        }
    )

    assert repairs[0].path == "people/casey.md"
    assert repairs[0].apply is True

    client = FakeClient(
        {
            "repairs": [
                {
                    "path": "people/casey.md",
                    "apply": False,
                    "summary": "No safe repair available.",
                    "content": "---\ntitle: Casey\n---\n\n# Casey\n",
                }
            ]
        }
    )
    engine = MigrationLLM(client=client)
    result = engine.repair_migration_pages(pages=({"path": "people/casey.md", "content": "# Casey"},))

    assert result[0].apply is False
    assert client.calls[0]["schema_name"] == "dory_migration_repair"


def test_parse_classification_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        parse_classification_response(
            {
                "doc_class": "bad",
                "canonicality": "canonical",
                "target_path": "x.md",
                "domain": "work",
                "entity_refs": [],
                "decision_refs": [],
                "time_scope": "current",
                "confidence": "high",
                "action": "route_final",
                "reason": "x",
            }
        )
