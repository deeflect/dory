from __future__ import annotations

from dory_core.entity_registry import EntityRegistry
from dory_core.migration_engine import MigrationEngine
from dory_core.migration_llm import MigrationLLM
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.types import MemoryWriteReq


def test_semantic_write_resolves_new_subjects_added_after_engine_init(tmp_path) -> None:
    root = tmp_path / "corpus"
    (root / "people").mkdir(parents=True)
    (root / "people" / "anna.md").write_text(
        "---\n"
        "title: Anna\n"
        "aliases:\n"
        "  - anna\n"
        "---\n"
        "# Anna\n",
        encoding="utf-8",
    )

    engine = SemanticWriteEngine(root)

    (root / "projects" / "rooster").mkdir(parents=True)
    (root / "projects" / "rooster" / "state.md").write_text(
        "---\n"
        "title: Rooster\n"
        "aliases:\n"
        "  - rooster\n"
        "---\n"
        "# Rooster\n",
        encoding="utf-8",
    )

    response = engine.write(
        MemoryWriteReq(
            action="write",
            kind="state",
            subject="rooster",
            content="Rooster is active.",
            scope="project",
            allow_canonical=True,
        )
    )

    assert response.resolved is True
    assert response.target_path == "projects/rooster/state.md"
    assert "Rooster is active." in (root / "projects" / "rooster" / "state.md").read_text(encoding="utf-8")


def test_migration_registers_canonical_subject_aliases_for_future_resolution(tmp_path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "profile.md").write_text("# Profile\n\nDee prefers async work.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "person_profile",
                    "canonicality": "canonical",
                    "target_path": "people/casey.md",
                    "domain": "mixed",
                    "entity_refs": ["person:casey", "person:jordan-example"],
                    "decision_refs": [],
                    "time_scope": "current",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "profile note",
                    "source_quality": "strong",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [
                        {
                            "ref": "person:casey",
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
                            "time_ref": "2026-04-14",
                            "confidence": "high",
                        }
                    ],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    registry = EntityRegistry(output_root / ".dory" / "entity-registry.db")
    match = registry.resolve("jordan example", family="person")
    assert match is not None
    assert match.entity_id == "person:casey"
    assert match.target_path == "people/casey.md"


def test_semantic_write_uses_llm_resolution_for_partial_subject_queries(tmp_path) -> None:
    root = tmp_path / "corpus"
    (root / "people").mkdir(parents=True)
    person_path = root / "people" / "alex-example.md"
    person_path.write_text(
        "---\n"
        "title: Alex Example\n"
        "---\n"
        "# Alex Example\n",
        encoding="utf-8",
    )

    class _FakeResolverClient:
        def generate_json(self, **_: object):
            return {
                "chosen_subject_ref": "person:alex-example",
                "confidence": "high",
                "ambiguous": False,
                "reason": "Partial query matches the known person title.",
            }

    engine = SemanticWriteEngine(root, resolver_client=_FakeResolverClient())
    response = engine.write(
        MemoryWriteReq(
            action="write",
            kind="fact",
            subject="anna zvi",
            content="Prefers async work.",
            scope="person",
            allow_canonical=True,
        )
    )

    assert response.resolved is True
    assert response.target_path == "people/alex-example.md"
    assert "Prefers async work." in person_path.read_text(encoding="utf-8")
