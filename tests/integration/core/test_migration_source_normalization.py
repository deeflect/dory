from __future__ import annotations

from pathlib import Path

from dory_core.frontmatter import load_markdown_document
from dory_core.migration_engine import MigrationEngine
from dory_core.migration_llm import MigrationLLM


def test_migration_forces_source_metadata_on_imported_evidence(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "legacy.md"
    note.write_text(
        "---\ntitle: Legacy\ncanonical: true\nsource_kind: human\nstatus: active\n---\n\nLegacy body.\n",
        encoding="utf-8",
    )

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "source_legacy",
                    "canonicality": "evidence",
                    "target_path": "sources/legacy/legacy.md",
                    "domain": "mixed",
                    "entity_refs": [],
                    "decision_refs": [],
                    "time_scope": "historical",
                    "confidence": "high",
                    "action": "store_as_source",
                    "reason": "legacy source",
                    "source_quality": "strong",
                    "resolution_mode": "evidence_only",
                    "quarantine_reason": None,
                    "entity_candidates": [],
                    "atoms": [],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    migrated = load_markdown_document((output_root / "sources" / "legacy" / "legacy.md").read_text(encoding="utf-8"))
    assert migrated.frontmatter["type"] == "source"
    assert migrated.frontmatter["canonical"] is False
    assert migrated.frontmatter["status"] == "done"
    assert migrated.frontmatter["source_kind"] == "legacy"
    assert migrated.frontmatter["confidence"] == "high"
