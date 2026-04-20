from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import sleep

import pytest

from dory_core.frontmatter import load_markdown_document
from dory_core.migration_events import MigrationRunEvent
from dory_core.migration_engine import MigrationEngine
from dory_core.migration_llm import MigrationLLM


def test_migration_bootstraps_canonical_pages(tmp_path: Path) -> None:
    fixture_root = Path("tests/fixtures/legacy_clawd_brain")
    output_root = tmp_path / "corpus"

    result = MigrationEngine(output_root).migrate(fixture_root)

    assert not (output_root / "core" / "user.md").exists()
    assert not (output_root / "core" / "soul.md").exists()
    assert not (output_root / "projects" / "rooster" / "state.md").exists()
    assert not (output_root / "concepts" / "openclaw.md").exists()
    assert (output_root / "sources" / "imported" / "user.md").exists()
    assert (output_root / "sources" / "imported" / "soul.md").exists()
    assert (output_root / "sources" / "imported" / "projects" / "rooster-spec.md").exists()
    assert (output_root / "digests" / "daily" / "2026-03-25-digest.md").exists()
    assert (output_root / "logs" / "sessions" / "2026-03-20-revenue-plan.md").exists()
    assert (output_root / result.report_path).exists()
    assert result.staged_count == 8
    assert result.written_count >= 7
    assert result.canonical_created_count == 0
    assert result.quarantined_count == 0
    assert result.stats.fallback_classified_count >= 1
    assert result.stats.atom_count == 0
    assert result.stats.duration_ms >= 0


def test_migration_preserves_evidence_without_heuristic_canonical_promotion(tmp_path: Path) -> None:
    fixture_root = Path("tests/fixtures/legacy_clawd_brain")
    output_root = tmp_path / "corpus"

    MigrationEngine(output_root).migrate(fixture_root)

    project_source = (output_root / "sources" / "imported" / "projects" / "rooster-spec.md").read_text(encoding="utf-8")
    assert "Rooster" in project_source
    assert not (output_root / "projects" / "rooster" / "state.md").exists()
    assert not (output_root / "concepts" / "openclaw.md").exists()


def test_migration_engine_respects_selected_paths(tmp_path: Path) -> None:
    fixture_root = Path("tests/fixtures/legacy_clawd_brain")
    output_root = tmp_path / "corpus"
    selected_paths = (
        fixture_root / "USER.md",
        fixture_root / "memory" / "daily" / "2026-03-25-digest.md",
    )

    result = MigrationEngine(output_root).migrate(fixture_root, selected_paths=selected_paths)

    assert result.staged_count == 2
    assert (output_root / "sources" / "imported" / "user.md").exists()
    assert (output_root / "digests" / "daily" / "2026-03-25-digest.md").exists()
    assert not (output_root / "projects" / "rooster" / "state.md").exists()
    assert not (output_root / "logs" / "sessions" / "2026-03-20-revenue-plan.md").exists()
    assert not (output_root / "concepts" / "openclaw.md").exists()


def test_migration_engine_stages_json_inputs_as_markdown_evidence(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    capture = legacy_root / "capture.json"
    capture.write_text('{"name":"Session Export","status":"active","items":["a","b"]}\n', encoding="utf-8")

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    assert result.staged_count == 1
    evidence = output_root / "sources" / "imported" / "root" / "capture.json.md"
    assert evidence.exists()
    document = load_markdown_document(evidence.read_text(encoding="utf-8"))
    assert document.frontmatter["type"] == "source"
    assert "# Session Export" in document.body
    assert "## Extracted Fields" in document.body
    assert "- status: active" in document.body
    assert "## Raw JSON" in document.body


def test_migration_engine_selected_paths_accept_json_inputs(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    markdown = legacy_root / "ignore.md"
    markdown.write_text("ignore me\n", encoding="utf-8")
    capture = legacy_root / "capture.json"
    capture.write_text('{"title":"Capture","kind":"session"}\n', encoding="utf-8")

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root, selected_paths=(capture,))

    assert result.staged_count == 1
    assert (output_root / "sources" / "imported" / "root" / "capture.json.md").exists()
    assert not (output_root / "sources" / "imported" / "root" / "ignore.md").exists()


@pytest.mark.parametrize(
    ("file_name", "raw_text", "expected_snippets"),
    [
        ("capture.txt", "Legacy scratch note\nSecond line\n", ("Source format: txt", "## Raw Text", "Legacy scratch note")),
        ("capture.yaml", "title: Legacy Capture\nstatus: active\n", ("Source format: yaml", "## Raw YAML", "title: Legacy Capture")),
        ("capture.yml", "title: Legacy Capture\nstatus: active\n", ("Source format: yml", "## Raw YML", "title: Legacy Capture")),
        ("capture.toml", 'title = "Legacy Capture"\nstatus = "active"\n', ("Source format: toml", "## Raw TOML", 'title = "Legacy Capture"')),
        (
            "capture.csv",
            "name,status\nRooster,active\nAtlas,parked\n",
            ("Source format: csv", "## Raw CSV", "name,status"),
        ),
    ],
)
def test_migration_engine_stages_textual_inputs_as_markdown_evidence(
    tmp_path: Path,
    file_name: str,
    raw_text: str,
    expected_snippets: tuple[str, ...],
) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    capture = legacy_root / file_name
    capture.write_text(raw_text, encoding="utf-8")

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    assert result.staged_count == 1
    evidence = output_root / "sources" / "imported" / "root" / f"{file_name}.md"
    assert evidence.exists()
    document = load_markdown_document(evidence.read_text(encoding="utf-8"))
    assert document.frontmatter["type"] == "source"
    for snippet in expected_snippets:
        assert snippet in document.body


def test_migration_engine_selected_paths_accept_textual_inputs(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    ignored = legacy_root / "ignore.md"
    ignored.write_text("ignore me\n", encoding="utf-8")
    note = legacy_root / "capture.txt"
    note.write_text("Legacy note\n", encoding="utf-8")
    config = legacy_root / "capture.toml"
    config.write_text('title = "Capture"\nsummary = "Keep this as evidence."\n', encoding="utf-8")

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root, selected_paths=(note, config))

    assert result.staged_count == 2
    assert (output_root / "sources" / "imported" / "root" / "capture.txt.md").exists()
    assert (output_root / "sources" / "imported" / "root" / "capture.toml.md").exists()
    assert not (output_root / "sources" / "imported" / "root" / "ignore.md").exists()


def test_migration_engine_stages_jsonl_transcripts_as_markdown_evidence(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    transcript = legacy_root / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-12T11:00:00Z",
                        "session_id": "session-a",
                        "role": "user",
                        "content": [{"type": "text", "text": "what did we decide for Rooster?"}],
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-12T11:00:01Z",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Rooster is the active focus this week."}],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    assert result.staged_count == 1
    evidence = output_root / "logs" / "sessions" / "imported" / "2026-04-12-session-a.md"
    assert evidence.exists()
    document = load_markdown_document(evidence.read_text(encoding="utf-8"))
    assert document.frontmatter["type"] == "session"
    assert "# session-a" in document.body
    assert "## Extracted Transcript" in document.body
    assert "- user: what did we decide for Rooster?" in document.body
    assert "- assistant: Rooster is the active focus this week." in document.body
    assert "## Raw JSONL" in document.body


def test_migration_engine_stages_ndjson_inputs_as_markdown_evidence(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    transcript = legacy_root / "events.ndjson"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-04-12T10:00:00Z", "type": "user_message", "message": "remember this pricing plan"}),
                json.dumps({"timestamp": "2026-04-12T10:00:03Z", "role": "assistant", "content": "Clawsy stays on the small Hetzner VPS."}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    assert result.staged_count == 1
    evidence = output_root / "logs" / "sessions" / "imported" / "2026-04-12-events.md"
    assert evidence.exists()
    document = load_markdown_document(evidence.read_text(encoding="utf-8"))
    assert document.frontmatter["type"] == "session"
    body = document.body
    assert "Source format: ndjson" in body
    assert "## Extracted Records" in body
    assert "- record_count: 2" in body
    assert "remember this pricing plan" in body
    assert "Clawsy stays on the small Hetzner VPS." in body


def test_migration_engine_promotes_deterministic_transcript_atoms_into_canonical_state(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    transcript = legacy_root / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-12T11:00:00Z",
                        "session_id": "session-a",
                        "role": "user",
                        "content": [{"type": "text", "text": "what did we decide for Rooster?"}],
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-12T11:00:01Z",
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Rooster is the active focus this week."}],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    artifact_root = output_root / "inbox" / "migration-documents" / Path(result.run_artifact_path).stem
    payload = json.loads((artifact_root / "session.json").read_text(encoding="utf-8"))
    project_state = output_root / "projects" / "rooster" / "state.md"

    assert payload["resolution_mode"] == "resolved"
    assert payload["classified"]["doc_class"] == "session_log"
    assert payload["used_llm_for_extraction"] is False
    assert project_state.exists()
    assert "Rooster is the active focus this week." in project_state.read_text(encoding="utf-8")
    assert result.stats.atom_count == 1
    assert payload["atoms"] == [
        {
            "confidence": "medium",
            "evidence_path": "logs/sessions/imported/2026-04-12-session-a.md",
            "kind": "project_update",
            "payload": {
                "summary": "Rooster is the active focus this week.",
                "title": "Rooster",
            },
            "subject_ref": "project:rooster",
            "time_ref": "2026-04-12",
        }
    ]


def test_migration_engine_promotes_typed_project_json_into_canonical_state(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    capture = legacy_root / "project.json"
    capture.write_text(
        json.dumps(
            {
                "kind": "project",
                "name": "Rooster",
                "summary": "Rooster is the active focus this week.",
                "status": "active",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    artifact_root = output_root / "inbox" / "migration-documents" / Path(result.run_artifact_path).stem
    payload = json.loads((artifact_root / "project.json").read_text(encoding="utf-8"))
    project_state = output_root / "projects" / "rooster" / "state.md"

    assert payload["resolution_mode"] == "resolved"
    assert payload["classified"]["doc_class"] == "source_imported"
    assert project_state.exists()
    assert "Rooster is the active focus this week." in project_state.read_text(encoding="utf-8")
    assert payload["atoms"] == [
        {
            "confidence": "medium",
            "evidence_path": "sources/imported/root/project.json.md",
            "kind": "project_update",
            "payload": {
                "summary": "Rooster is the active focus this week.",
                "title": "Rooster",
            },
            "subject_ref": "project:rooster",
            "time_ref": None,
        }
    ]


def test_migration_engine_promotes_typed_person_json_into_canonical_state(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    capture = legacy_root / "person.json"
    capture.write_text(
        json.dumps(
            {
                "type": "person",
                "name": "Casey",
                "summary": "Prefers async work.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    artifact_root = output_root / "inbox" / "migration-documents" / Path(result.run_artifact_path).stem
    payload = json.loads((artifact_root / "person.json").read_text(encoding="utf-8"))
    person_page = output_root / "people" / "casey.md"

    assert payload["resolution_mode"] == "resolved"
    assert person_page.exists()
    assert "Prefers async work." in person_page.read_text(encoding="utf-8")
    assert payload["atoms"] == [
        {
            "confidence": "medium",
            "evidence_path": "sources/imported/root/person.json.md",
            "kind": "person_fact",
            "payload": {
                "summary": "Prefers async work.",
                "title": "Casey",
            },
            "subject_ref": "person:casey",
            "time_ref": None,
        }
    ]


def test_migration_engine_promotes_typed_decision_json_into_canonical_state(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    capture = legacy_root / "decision.json"
    capture.write_text(
        json.dumps(
            {
                "type": "decision",
                "decision": "Use Dory as the shared memory layer.",
                "summary": "Use Dory as the shared memory layer.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    artifact_root = output_root / "inbox" / "migration-documents" / Path(result.run_artifact_path).stem
    payload = json.loads((artifact_root / "decision.json").read_text(encoding="utf-8"))
    decision_page = output_root / "decisions" / "use-dory-as-the-shared-memory-layer.md"

    assert payload["resolution_mode"] == "resolved"
    assert decision_page.exists()
    assert "Use Dory as the shared memory layer." in decision_page.read_text(encoding="utf-8")
    assert payload["atoms"] == [
        {
            "confidence": "medium",
            "evidence_path": "sources/imported/root/decision.json.md",
            "kind": "decision",
            "payload": {
                "summary": "Use Dory as the shared memory layer.",
                "title": "Use Dory as the shared memory layer.",
            },
            "subject_ref": "decision:use-dory-as-the-shared-memory-layer",
            "time_ref": None,
        }
    ]


def test_migration_engine_promotes_typed_concept_json_into_canonical_state(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    capture = legacy_root / "concept.json"
    capture.write_text(
        json.dumps(
            {
                "kind": "concept",
                "name": "Wake",
                "summary": "Wake is the bounded startup context block.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    artifact_root = output_root / "inbox" / "migration-documents" / Path(result.run_artifact_path).stem
    payload = json.loads((artifact_root / "concept.json").read_text(encoding="utf-8"))
    concept_page = output_root / "concepts" / "wake.md"

    assert payload["resolution_mode"] == "resolved"
    assert concept_page.exists()
    assert "Wake is the bounded startup context block." in concept_page.read_text(encoding="utf-8")
    assert payload["atoms"] == [
        {
            "confidence": "medium",
            "evidence_path": "sources/imported/root/concept.json.md",
            "kind": "concept_claim",
            "payload": {
                "summary": "Wake is the bounded startup context block.",
                "title": "Wake",
            },
            "subject_ref": "concept:wake",
            "time_ref": None,
        }
    ]


def test_migration_engine_promotes_explicit_project_export_schema_json_into_canonical_state(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    capture = legacy_root / "project-export.json"
    capture.write_text(
        json.dumps(
            {
                "schema": "dory.project_export.v1",
                "entity": {
                    "title": "Rooster",
                },
                "current_state": {
                    "summary": "Rooster is the active focus this week.",
                    "status": "active",
                },
                "time_ref": "2026-04-14",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    artifact_root = output_root / "inbox" / "migration-documents" / Path(result.run_artifact_path).stem
    payload = json.loads((artifact_root / "project-export.json").read_text(encoding="utf-8"))
    project_state = output_root / "projects" / "rooster" / "state.md"

    assert payload["resolution_mode"] == "resolved"
    assert project_state.exists()
    assert "Rooster is the active focus this week." in project_state.read_text(encoding="utf-8")
    assert payload["atoms"] == [
        {
            "confidence": "medium",
            "evidence_path": "sources/imported/root/project-export.json.md",
            "kind": "project_update",
            "payload": {
                "summary": "Rooster is the active focus this week.",
                "title": "Rooster",
            },
            "subject_ref": "project:rooster",
            "time_ref": "2026-04-14",
        }
    ]


def test_migration_engine_keeps_unknown_schema_json_as_evidence_only(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    capture = legacy_root / "unknown-export.json"
    capture.write_text(
        json.dumps(
            {
                "schema": "vendor.unknown_export.v1",
                "entity": {
                    "title": "Rooster",
                },
                "current_state": {
                    "summary": "Rooster is the active focus this week.",
                },
                "time_ref": "2026-04-14",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    artifact_root = output_root / "inbox" / "migration-documents" / Path(result.run_artifact_path).stem
    payload = json.loads((artifact_root / "unknown-export.json").read_text(encoding="utf-8"))

    assert payload["resolution_mode"] == "evidence_only"
    assert payload["atoms"] == []
    assert not (output_root / "projects" / "rooster" / "state.md").exists()
    assert (output_root / "sources" / "imported" / "root" / "unknown-export.json.md").exists()
    assert result.canonical_created_count == 0


def test_migration_normalizes_evidence_frontmatter_and_strips_frontmatter_from_summaries(tmp_path: Path) -> None:
    fixture_root = Path("tests/fixtures/legacy_clawd_brain")
    output_root = tmp_path / "corpus"

    MigrationEngine(output_root).migrate(fixture_root)

    daily_digest = load_markdown_document((output_root / "digests" / "daily" / "2026-03-25-digest.md").read_text(encoding="utf-8"))
    session_log = load_markdown_document((output_root / "logs" / "sessions" / "2026-03-20-revenue-plan.md").read_text(encoding="utf-8"))
    person_source = load_markdown_document((output_root / "sources" / "imported" / "people" / "casey.md").read_text(encoding="utf-8"))

    assert daily_digest.frontmatter["type"] == "digest-daily"
    assert session_log.frontmatter["type"] == "session"
    assert person_source.frontmatter["type"] == "source"


def test_migration_persists_aliases_and_only_links_existing_evidence(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "notes.md"
    note.write_text("# Profile\n\nDee / Jordan prefers async work.\n", encoding="utf-8")

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
                            "evidence_path": "sources/imported/notes.md",
                            "time_ref": "2026-04-13",
                            "confidence": "high",
                        },
                        {
                            "kind": "person_fact",
                            "subject_ref": "person:casey",
                            "payload": {"summary": "Should be ignored missing evidence."},
                            "evidence_path": "sources/imported/missing.md",
                            "time_ref": "2026-04-13",
                            "confidence": "high",
                        },
                    ],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    person = load_markdown_document((output_root / "people" / "casey.md").read_text(encoding="utf-8"))
    aliases = {value.lower() for value in person.frontmatter["aliases"]}
    assert "jordan example" in aliases
    assert "sources/imported/root/notes.md" in person.body
    assert "sources/imported/missing.md" not in person.body


def test_migration_engine_uses_llm_when_available(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "notes.md"
    note.write_text("# Profile\n\nJordan prefers async work and Rooster is active.\n", encoding="utf-8")

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
                            "ref": "person:jordan-example",
                            "display_name": "Casey",
                            "aliases": ["Casey"],
                            "confidence": "high",
                        }
                    ],
                    "atoms": [
                        {
                            "kind": "person_fact",
                            "subject_ref": "person:jordan-example",
                            "payload": {"title": "Casey", "summary": "Prefers async work."},
                            "evidence_path": "people/casey.md",
                            "time_ref": "2026-04-13",
                            "confidence": "high",
                        }
                    ],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    person_page = output_root / "people" / "casey.md"
    assert person_page.exists()
    assert "Prefers async work." in person_page.read_text(encoding="utf-8")
    run_artifact = output_root / result.run_artifact_path
    assert run_artifact.exists()
    assert '"person:jordan-example": "person:casey"' in run_artifact.read_text(encoding="utf-8")


def test_migration_engine_does_not_alias_cross_family_entity_refs(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "project.md"
    note.write_text("# Project\n\nDee is working on AtlasApp.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "project_state",
                    "canonicality": "canonical",
                    "target_path": "projects/atlasapp/state.md",
                    "domain": "mixed",
                    "entity_refs": ["project:atlasapp", "person:casey"],
                    "decision_refs": [],
                    "time_scope": "current",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "project note",
                    "source_quality": "strong",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [
                        {
                            "ref": "project:atlasapp",
                            "display_name": "AtlasApp",
                            "aliases": [],
                            "confidence": "high",
                        },
                        {
                            "ref": "person:casey",
                            "display_name": "Casey",
                            "aliases": [],
                            "confidence": "high",
                        },
                    ],
                    "atoms": [
                        {
                            "kind": "person_fact",
                            "subject_ref": "person:casey",
                            "payload": {"summary": "Casey is working on AtlasApp."},
                            "evidence_path": "projects/atlasapp/state.md",
                            "time_ref": "2026-04-13",
                            "confidence": "high",
                        },
                        {
                            "kind": "project_update",
                            "subject_ref": "project:atlasapp",
                            "payload": {"summary": "AtlasApp is active."},
                            "evidence_path": "projects/atlasapp/state.md",
                            "time_ref": "2026-04-13",
                            "confidence": "high",
                        },
                    ],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    run_artifact = (output_root / result.run_artifact_path).read_text(encoding="utf-8")
    assert '"person:casey": "project:atlasapp"' not in run_artifact
    assert (output_root / "people" / "casey.md").exists()
    assert (output_root / "projects" / "atlasapp" / "state.md").exists()


def test_migration_engine_clusters_entities_across_documents_with_llm_resolution(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "a.md").write_text("# Profile\n\nDee prefers async work.\n", encoding="utf-8")
    (legacy_root / "b.md").write_text("# Notes\n\nJordan Example is the same person.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, user_prompt: str, **_: object):
            if schema_name == "dory_migration_document" and "a.md" in user_prompt:
                return {
                    "doc_class": "person_profile",
                    "canonicality": "canonical",
                    "target_path": "people/casey.md",
                    "domain": "mixed",
                    "entity_refs": ["person:casey"],
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
                            "time_ref": "2026-04-13",
                            "confidence": "high",
                        }
                    ],
                }
            if schema_name == "dory_migration_document" and "b.md" in user_prompt:
                return {
                    "doc_class": "person_profile",
                    "canonicality": "canonical",
                    "target_path": "people/jordan-example.md",
                    "domain": "mixed",
                    "entity_refs": ["person:jordan-example"],
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
                            "ref": "person:jordan-example",
                            "display_name": "Jordan Example",
                            "aliases": ["Casey"],
                            "confidence": "high",
                        }
                    ],
                    "atoms": [
                        {
                            "kind": "person_fact",
                            "subject_ref": "person:jordan-example",
                            "payload": {"summary": "Is the same person as Casey."},
                            "evidence_path": "people/jordan-example.md",
                            "time_ref": "2026-04-13",
                            "confidence": "high",
                        }
                    ],
                }
            if schema_name == "dory_migration_entity_resolution":
                return {
                    "clusters": [
                        {
                            "canonical_ref": "person:casey",
                            "family": "person",
                            "display_name": "Casey",
                            "aliases": ["Jordan Example"],
                            "member_keys": [
                                "a.md::0::person:casey",
                                "b.md::0::person:jordan-example",
                            ],
                        }
                    ]
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    dee_page = output_root / "people" / "casey.md"
    assert dee_page.exists()
    dee_text = dee_page.read_text(encoding="utf-8")
    assert "Prefers async work." in dee_text
    assert "Is the same person as Casey." in dee_text
    assert not (output_root / "people" / "jordan-example.md").exists()
    run_artifact = (output_root / result.run_artifact_path).read_text(encoding="utf-8")
    assert '"person:jordan-example": "person:casey"' in run_artifact


def test_migration_engine_records_entity_resolution_fallback_in_run_artifact(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "a.md").write_text("# Profile\n\nDee prefers async work.\n", encoding="utf-8")
    (legacy_root / "b.md").write_text("# Notes\n\nJordan Example is the same person.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, user_prompt: str, **_: object):
            if schema_name == "dory_migration_document" and "a.md" in user_prompt:
                return {
                    "doc_class": "person_profile",
                    "canonicality": "canonical",
                    "target_path": "people/casey.md",
                    "domain": "mixed",
                    "entity_refs": ["person:casey"],
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
                            "time_ref": "2026-04-13",
                            "confidence": "high",
                        }
                    ],
                }
            if schema_name == "dory_migration_document" and "b.md" in user_prompt:
                return {
                    "doc_class": "person_profile",
                    "canonicality": "canonical",
                    "target_path": "people/jordan-example.md",
                    "domain": "mixed",
                    "entity_refs": ["person:jordan-example"],
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
                            "ref": "person:jordan-example",
                            "display_name": "Jordan Example",
                            "aliases": ["Casey"],
                            "confidence": "high",
                        }
                    ],
                    "atoms": [
                        {
                            "kind": "person_fact",
                            "subject_ref": "person:jordan-example",
                            "payload": {"summary": "Is the same person as Casey."},
                            "evidence_path": "people/jordan-example.md",
                            "time_ref": "2026-04-13",
                            "confidence": "high",
                        }
                    ],
                }
            if schema_name == "dory_migration_entity_resolution":
                raise RuntimeError("entity resolution exploded")
            if schema_name == "dory_migration_audit":
                return {
                    "audits": [
                        {
                            "path": "people/casey.md",
                            "verdict": "pass",
                            "summary": "Looks grounded.",
                            "issues": [],
                        },
                        {
                            "path": "people/jordan-example.md",
                            "verdict": "pass",
                            "summary": "Looks grounded.",
                            "issues": [],
                        },
                    ]
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    run_artifact = json.loads((output_root / result.run_artifact_path).read_text(encoding="utf-8"))
    assert run_artifact["fallback_warnings"] == [
        {
            "message": "RuntimeError: entity resolution exploded",
            "scope": "person",
            "stage": "entity_resolution",
        }
    ]


def test_migration_engine_writes_audit_artifact_when_llm_audit_returns_findings(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "note.md").write_text("# Profile\n\nDee prefers async work.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "person_profile",
                    "canonicality": "canonical",
                    "target_path": "people/casey.md",
                    "domain": "mixed",
                    "entity_refs": ["person:casey"],
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
                            "aliases": [],
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
            if schema_name == "dory_migration_audit":
                return {
                    "audits": [
                        {
                            "path": "people/casey.md",
                            "verdict": "review",
                            "summary": "Needs a second confirming source.",
                            "issues": ["single-source canonical page"],
                        }
                    ]
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    run_artifact = json.loads((output_root / result.run_artifact_path).read_text(encoding="utf-8"))
    audit_rel = Path(run_artifact["audit_artifact_path"])
    assert audit_rel == Path("inbox/migration-runs") / f"{Path(result.run_artifact_path).stem}.audit.json"
    audit_payload = json.loads((output_root / audit_rel).read_text(encoding="utf-8"))
    assert audit_payload["audits"][0]["verdict"] == "review"
    report_text = (output_root / result.report_path).read_text(encoding="utf-8")
    assert "## QA Findings" in report_text


def test_migration_engine_records_audit_fallback_in_run_artifact(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "note.md").write_text("# Profile\n\nDee prefers async work.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "person_profile",
                    "canonicality": "canonical",
                    "target_path": "people/casey.md",
                    "domain": "mixed",
                    "entity_refs": ["person:casey"],
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
                            "aliases": [],
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
            if schema_name == "dory_migration_audit":
                raise RuntimeError("audit exploded")
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    run_artifact = json.loads((output_root / result.run_artifact_path).read_text(encoding="utf-8"))
    assert run_artifact["fallback_warnings"] == [
        {
            "message": "RuntimeError: audit exploded",
            "scope": None,
            "stage": "audit",
        }
    ]
    report_text = (output_root / result.report_path).read_text(encoding="utf-8")
    assert "## Fallback Warnings" in report_text


def test_migration_engine_repairs_flagged_pages_and_reaudits(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "note.md").write_text("# Profile\n\nDee prefers async work.\n", encoding="utf-8")

    class _FakeClient:
        def __init__(self) -> None:
            self.audit_calls = 0

        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "person_profile",
                    "canonicality": "canonical",
                    "target_path": "people/casey.md",
                    "domain": "mixed",
                    "entity_refs": ["person:casey"],
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
                            "aliases": [],
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
            if schema_name == "dory_migration_audit":
                self.audit_calls += 1
                if self.audit_calls == 1:
                    return {
                        "audits": [
                            {
                                "path": "people/casey.md",
                                "verdict": "review",
                                "summary": "Current summary is too absolute for one source.",
                                "issues": ["single-source canonical page"],
                            }
                        ]
                    }
                return {
                    "audits": [
                        {
                            "path": "people/casey.md",
                            "verdict": "pass",
                            "summary": "Looks grounded after repair.",
                            "issues": [],
                        }
                    ]
                }
            if schema_name == "dory_migration_repair":
                return {
                    "repairs": [
                        {
                            "path": "people/casey.md",
                            "apply": True,
                            "summary": "Softened the summary and preserved evidence.",
                            "content": (
                                "---\n"
                                "title: Casey\n"
                                "type: person\n"
                                "slug: casey\n"
                                "domain: mixed\n"
                                "canonical: true\n"
                                "source_kind: canonical\n"
                                "has_timeline: true\n"
                                "aliases: []\n"
                                "---\n\n"
                                "# Casey\n\n"
                                "## Summary\n\n"
                                "Current evidence suggests Casey prefers async work.\n\n"
                                "## Timeline\n\n"
                                "- 2026-04-13: Prefers async work. (`sources/imported/root/note.md`)\n\n"
                                "## Evidence\n\n"
                                "- `sources/imported/root/note.md`\n"
                            ),
                        }
                    ]
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    page_text = (output_root / "people" / "casey.md").read_text(encoding="utf-8")
    assert "Current evidence suggests Casey prefers async work." in page_text
    run_artifact = json.loads((output_root / result.run_artifact_path).read_text(encoding="utf-8"))
    assert run_artifact["repairs"][0]["path"] == "people/casey.md"
    assert run_artifact["repairs"][0]["apply"] is True
    assert run_artifact["audits"][0]["verdict"] == "pass"
    report_text = (output_root / result.report_path).read_text(encoding="utf-8")
    assert "## Repairs Applied" in report_text


def test_migration_engine_records_repair_fallback_in_run_artifact(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "note.md").write_text("# Profile\n\nDee prefers async work.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "person_profile",
                    "canonicality": "canonical",
                    "target_path": "people/casey.md",
                    "domain": "mixed",
                    "entity_refs": ["person:casey"],
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
                            "aliases": [],
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
            if schema_name == "dory_migration_audit":
                return {
                    "audits": [
                        {
                            "path": "people/casey.md",
                            "verdict": "review",
                            "summary": "Needs softer wording.",
                            "issues": ["single-source canonical page"],
                        }
                    ]
                }
            if schema_name == "dory_migration_repair":
                raise RuntimeError("repair exploded")
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    run_artifact = json.loads((output_root / result.run_artifact_path).read_text(encoding="utf-8"))
    assert run_artifact["fallback_warnings"] == [
        {
            "message": "RuntimeError: repair exploded",
            "scope": None,
            "stage": "repair",
        }
    ]


def test_migration_engine_repairs_flagged_pages_with_llm(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "note.md").write_text("# Profile\n\nDee prefers async work.\n", encoding="utf-8")

    class _FakeClient:
        def __init__(self) -> None:
            self.audit_calls = 0

        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "person_profile",
                    "canonicality": "canonical",
                    "target_path": "people/casey.md",
                    "domain": "mixed",
                    "entity_refs": ["person:casey"],
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
                            "aliases": [],
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
            if schema_name == "dory_migration_audit":
                self.audit_calls += 1
                if self.audit_calls == 1:
                    return {
                        "audits": [
                            {
                                "path": "people/casey.md",
                                "verdict": "review",
                                "summary": "Summary overstates certainty.",
                                "issues": ["overclaiming"],
                            }
                        ]
                    }
                return {
                    "audits": [
                        {
                            "path": "people/casey.md",
                            "verdict": "pass",
                            "summary": "Grounded after repair.",
                            "issues": [],
                        }
                    ]
                }
            if schema_name == "dory_migration_repair":
                return {
                    "repairs": [
                        {
                            "path": "people/casey.md",
                            "apply": True,
                            "summary": "Narrowed the page to grounded evidence.",
                            "content": (
                                "---\n"
                                "title: Casey\n"
                                "type: person\n"
                                "status: active\n"
                                "canonical: true\n"
                                "aliases: []\n"
                                "entity_id: person:casey\n"
                                "---\n\n"
                                "# Casey\n\n"
                                "## Summary\n"
                                "Prefers async work.\n\n"
                                "## Evidence\n"
                                "- sources/imported/root/note.md\n"
                            ),
                        }
                    ]
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    page_text = (output_root / "people" / "casey.md").read_text(encoding="utf-8")
    assert "Prefers async work." in page_text
    run_artifact = json.loads((output_root / result.run_artifact_path).read_text(encoding="utf-8"))
    repair_rel = Path(run_artifact["repair_artifact_path"])
    assert repair_rel == Path("inbox/migration-runs") / f"{Path(result.run_artifact_path).stem}.repair.json"
    repair_payload = json.loads((output_root / repair_rel).read_text(encoding="utf-8"))
    assert repair_payload["repairs"][0]["apply"] is True
    audit_rel = Path(run_artifact["audit_artifact_path"])
    audit_payload = json.loads((output_root / audit_rel).read_text(encoding="utf-8"))
    assert audit_payload["audits"][0]["verdict"] == "pass"
    report_text = (output_root / result.report_path).read_text(encoding="utf-8")
    assert "## Repairs Applied" in report_text


def test_migration_ignores_junk_markdown_roots(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    cache_dir = legacy_root / ".pytest_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "README.md").write_text("cache junk\n", encoding="utf-8")
    (legacy_root / "note.md").write_text("# Note\n\nreal content\n", encoding="utf-8")

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root).migrate(legacy_root)

    assert result.staged_count == 1
    assert not (output_root / "inbox" / "pytest-cache-readme.md").exists()


def test_migration_writes_document_artifacts(tmp_path: Path) -> None:
    fixture_root = Path("tests/fixtures/legacy_clawd_brain")
    output_root = tmp_path / "corpus"

    result = MigrationEngine(output_root).migrate(fixture_root)

    artifact_root = output_root / "inbox" / "migration-documents" / Path(result.run_artifact_path).stem
    user_artifact = artifact_root / "USER.json"
    assert user_artifact.exists()
    payload = json.loads(user_artifact.read_text(encoding="utf-8"))
    assert payload["path"] == "USER.md"
    assert payload["resolution_mode"] == "evidence_only"
    assert payload["classified"]["target_path"] == "sources/imported/user.md"


def test_migration_document_artifact_records_llm_fallback_reason(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "note.md"
    note.write_text("Rooster is active.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                raise RuntimeError("document extraction exploded")
            if schema_name == "dory_migration_classification":
                return {
                    "doc_class": "source_imported",
                    "canonicality": "evidence",
                    "target_path": "sources/imported/note.md",
                    "domain": "mixed",
                    "entity_refs": [],
                    "decision_refs": [],
                    "time_scope": "mixed",
                    "confidence": "medium",
                    "action": "store_as_source",
                    "reason": "fallback classification",
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    artifact_root = output_root / "inbox" / "migration-documents" / Path(result.run_artifact_path).stem
    payload = json.loads((artifact_root / "note.json").read_text(encoding="utf-8"))

    assert payload["used_llm_for_classification"] is True
    assert payload["used_llm_for_extraction"] is False
    assert payload["fallback_reasons"] == [
        "document_extraction_failed: RuntimeError: document extraction exploded"
    ]


def test_migration_engine_salvages_valid_llm_atoms_when_some_are_invalid(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "digest.md"
    note.write_text("Rooster is active again.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "digest_daily",
                    "canonicality": "evidence",
                    "target_path": "digests/daily/2026-04-13-digest.md",
                    "domain": "work",
                    "entity_refs": ["project:rooster"],
                    "decision_refs": [],
                    "time_scope": "current",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "daily digest",
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
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    project_state = output_root / "projects" / "rooster" / "state.md"
    assert project_state.exists()
    assert "Rooster is active again." in project_state.read_text(encoding="utf-8")


def test_migration_engine_ignores_core_subject_atoms_during_canonical_synthesis(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "SOUL.md"
    note.write_text("Direct, pragmatic assistant.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "core_soul",
                    "canonicality": "canonical",
                    "target_path": "core/soul.md",
                    "domain": "mixed",
                    "entity_refs": [],
                    "decision_refs": [],
                    "time_scope": "timeless",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "legacy soul doc",
                    "source_quality": "strong",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [],
                    "atoms": [
                        {
                            "kind": "concept_claim",
                            "subject_ref": "core:soul",
                            "payload": {"summary": "Direct and pragmatic tone."},
                            "evidence_path": "core/soul.md",
                            "time_ref": "2026-04-13",
                            "confidence": "medium",
                        }
                    ],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    assert (output_root / "core" / "soul.md").exists()
    assert result.canonical_created_count >= 1


def test_migration_engine_maps_core_soul_content_into_voice_section(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "SOUL.md"
    note.write_text("Direct, pragmatic assistant.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "core_soul",
                    "canonicality": "canonical",
                    "target_path": "core/soul.md",
                    "domain": "mixed",
                    "entity_refs": [],
                    "decision_refs": [],
                    "time_scope": "timeless",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "legacy soul doc",
                    "source_quality": "strong",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [],
                    "atoms": [],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    rendered = (output_root / "core" / "soul.md").read_text(encoding="utf-8")

    assert "## Voice\nDirect, pragmatic assistant." in rendered
    assert "sources/imported/root/SOUL.md" in rendered


def test_migration_engine_maps_core_env_content_into_environment_section(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "ENV.md"
    note.write_text("/srv/dory runs the local stack.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "core_env",
                    "canonicality": "canonical",
                    "target_path": "core/env.md",
                    "domain": "mixed",
                    "entity_refs": [],
                    "decision_refs": [],
                    "time_scope": "current",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "legacy env doc",
                    "source_quality": "strong",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [],
                    "atoms": [],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    rendered = (output_root / "core" / "env.md").read_text(encoding="utf-8")

    assert "## Environment\n/srv/dory runs the local stack." in rendered
    assert "sources/imported/root/ENV.md" in rendered


def test_migration_engine_maps_core_active_content_into_current_focus_section(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "ACTIVE.md"
    note.write_text("Finish corpus migration hardening.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "core_active",
                    "canonicality": "canonical",
                    "target_path": "core/active.md",
                    "domain": "mixed",
                    "entity_refs": [],
                    "decision_refs": [],
                    "time_scope": "current",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "legacy active doc",
                    "source_quality": "strong",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [],
                    "atoms": [],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    rendered = (output_root / "core" / "active.md").read_text(encoding="utf-8")

    assert "## Current Focus\nFinish corpus migration hardening." in rendered
    assert "sources/imported/root/ACTIVE.md" in rendered


def test_migration_engine_preserves_date_from_evidence_path_in_core_timeline(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "2026-04-13-ACTIVE.md"
    note.write_text("Finish corpus migration hardening.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "core_active",
                    "canonicality": "canonical",
                    "target_path": "core/active.md",
                    "domain": "mixed",
                    "entity_refs": [],
                    "decision_refs": [],
                    "time_scope": "current",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "legacy active doc",
                    "source_quality": "strong",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [],
                    "atoms": [],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    rendered = load_markdown_document((output_root / "core" / "active.md").read_text(encoding="utf-8"))

    assert "2026-04-13: Finish corpus migration hardening." in rendered.body


def test_migration_engine_preserves_date_from_structured_frontmatter_in_core_timeline(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "ACTIVE.md"
    note.write_text(
        "---\n"
        "date: 2026-04-13\n"
        "---\n"
        "\n"
        "Finish corpus migration hardening.\n",
        encoding="utf-8",
    )

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "core_active",
                    "canonicality": "canonical",
                    "target_path": "core/active.md",
                    "domain": "mixed",
                    "entity_refs": [],
                    "decision_refs": [],
                    "time_scope": "current",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "legacy active doc",
                    "source_quality": "strong",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [],
                    "atoms": [],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    rendered = load_markdown_document((output_root / "core" / "active.md").read_text(encoding="utf-8"))

    assert "2026-04-13: Finish corpus migration hardening." in rendered.body


def test_migration_engine_maps_core_defaults_content_into_default_operating_assumptions_section(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "DEFAULTS.md"
    note.write_text("Prefer bounded repair passes before broad rewrites.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "core_defaults",
                    "canonicality": "canonical",
                    "target_path": "core/defaults.md",
                    "domain": "mixed",
                    "entity_refs": [],
                    "decision_refs": [],
                    "time_scope": "timeless",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "legacy defaults doc",
                    "source_quality": "strong",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [],
                    "atoms": [],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    rendered = (output_root / "core" / "defaults.md").read_text(encoding="utf-8")

    assert "## Default Operating Assumptions\nPrefer bounded repair passes before broad rewrites." in rendered
    assert "sources/imported/root/DEFAULTS.md" in rendered


def test_migration_engine_preserves_root_core_docs_as_imported_evidence(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "USER.md"
    note.write_text("Casey prefers terse working notes.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "core_user",
                    "canonicality": "canonical",
                    "target_path": "core/user.md",
                    "domain": "mixed",
                    "entity_refs": [],
                    "decision_refs": [],
                    "time_scope": "timeless",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "legacy user doc",
                    "source_quality": "strong",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [
                        {
                            "ref": "person:casey",
                            "display_name": "Casey",
                            "aliases": [],
                            "confidence": "high",
                        }
                    ],
                    "atoms": [
                        {
                            "kind": "person_fact",
                            "subject_ref": "person:casey",
                            "payload": {"summary": "Casey prefers terse working notes."},
                            "evidence_path": "wrong/path.md",
                            "time_ref": "2026-04-13",
                            "confidence": "high",
                        }
                    ],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    evidence_path = output_root / "sources" / "imported" / "root" / "USER.md"
    core_user = output_root / "core" / "user.md"

    assert evidence_path.exists()
    assert core_user.exists()
    rendered = core_user.read_text(encoding="utf-8")
    assert "sources/imported/root/USER.md" in rendered
    assert "core/user.md" not in rendered.split("## Evidence", 1)[1]


def test_migration_engine_keeps_fallback_migration_evidence_only(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    (legacy_root / "memory" / "projects").mkdir(parents=True)
    (legacy_root / "memory" / "tools").mkdir(parents=True)

    user = legacy_root / "USER.md"
    user.write_text(
        "# USER.md — About Alex\n\n"
        "- Name: Alex\n"
        "- Works async-first\n"
        "- Focused on systems that ship\n",
        encoding="utf-8",
    )
    project = legacy_root / "memory" / "projects" / "atlas-spec.md"
    project.write_text(
        "# Atlas\n\n"
        "Atlas is the active project focus this week.\n",
        encoding="utf-8",
    )
    concept = legacy_root / "memory" / "tools" / "nimbus.md"
    concept.write_text(
        "# Nimbus\n\n"
        "Nimbus is a reusable orchestration concept.\n",
        encoding="utf-8",
    )

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root).migrate(legacy_root)

    alex = load_markdown_document((output_root / "sources" / "imported" / "user.md").read_text(encoding="utf-8"))
    atlas = load_markdown_document((output_root / "sources" / "imported" / "projects" / "atlas-spec.md").read_text(encoding="utf-8"))
    nimbus = load_markdown_document((output_root / "sources" / "imported" / "tools" / "nimbus.md").read_text(encoding="utf-8"))

    assert alex.frontmatter["type"] == "source"
    assert "Works async-first" in alex.body
    assert "Atlas is the active project focus this week." in atlas.body
    assert "Nimbus is a reusable orchestration concept." in nimbus.body
    assert not (output_root / "people" / "alex.md").exists()
    assert not (output_root / "projects" / "atlas" / "state.md").exists()
    assert not (output_root / "concepts" / "nimbus.md").exists()


def test_migration_engine_persists_contradictions_in_run_artifact(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    (legacy_root / "memory" / "archive" / "projects").mkdir(parents=True)
    (legacy_root / "memory" / "daily").mkdir(parents=True)
    archive_note = legacy_root / "memory" / "archive" / "projects" / "rooster-old.md"
    daily_note = legacy_root / "memory" / "daily" / "2026-04-13-digest.md"
    archive_note.write_text("Rooster was parked.\n", encoding="utf-8")
    daily_note.write_text("Rooster is the active focus.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, user_prompt: str, **_: object):
            path_hint = user_prompt.splitlines()[1]
            if "archive/projects" in path_hint:
                return {
                    "doc_class": "source_legacy",
                    "canonicality": "evidence",
                    "target_path": "sources/legacy/projects/rooster-old.md",
                    "domain": "work",
                    "entity_refs": ["project:rooster"],
                    "decision_refs": [],
                    "time_scope": "historical",
                    "confidence": "medium",
                    "action": "store_as_source",
                    "reason": "legacy archive",
                    "source_quality": "mixed",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [
                        {"ref": "project:rooster", "display_name": "Rooster", "aliases": [], "confidence": "medium"}
                    ],
                    "atoms": [
                        {
                            "kind": "project_update",
                            "subject_ref": "project:rooster",
                            "payload": {"title": "Rooster", "summary": "Rooster was parked."},
                            "evidence_path": "sources/legacy/projects/rooster-old.md",
                            "time_ref": "2026-04-01",
                            "confidence": "medium",
                        }
                    ],
                }
            return {
                "doc_class": "digest_daily",
                "canonicality": "evidence",
                "target_path": "digests/daily/2026-04-13-digest.md",
                "domain": "work",
                "entity_refs": ["project:rooster"],
                "decision_refs": [],
                "time_scope": "current",
                "confidence": "high",
                "action": "route_final",
                "reason": "daily digest",
                "source_quality": "strong",
                "resolution_mode": "resolved",
                "quarantine_reason": None,
                "entity_candidates": [
                    {"ref": "project:rooster", "display_name": "Rooster", "aliases": [], "confidence": "high"}
                ],
                "atoms": [
                    {
                        "kind": "project_update",
                        "subject_ref": "project:rooster",
                        "payload": {"title": "Rooster", "summary": "Rooster is the active focus."},
                        "evidence_path": "digests/daily/2026-04-13-digest.md",
                        "time_ref": "2026-04-13",
                        "confidence": "high",
                    }
                ],
            }

    output_root = tmp_path / "corpus"
    result = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    run_artifact = output_root / result.run_artifact_path
    payload = run_artifact.read_text(encoding="utf-8")
    assert '"contradictions": [' in payload
    assert "project:rooster" in payload


def test_migration_engine_preserves_legacy_time_refs_in_canonical_timeline(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    (legacy_root / "memory" / "archive" / "projects").mkdir(parents=True)
    archived_project = legacy_root / "memory" / "archive" / "projects" / "rooster-old.md"
    archived_project.write_text("Rooster was parked.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "project_state",
                    "canonicality": "canonical",
                    "target_path": "projects/rooster/state.md",
                    "domain": "work",
                    "entity_refs": ["project:rooster"],
                    "decision_refs": [],
                    "time_scope": "historical",
                    "confidence": "high",
                    "action": "route_final",
                    "reason": "archived project state",
                    "source_quality": "strong",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [
                        {"ref": "project:rooster", "display_name": "Rooster", "aliases": [], "confidence": "high"}
                    ],
                    "atoms": [
                        {
                            "kind": "project_update",
                            "subject_ref": "project:rooster",
                            "payload": {"title": "Rooster", "summary": "Rooster was parked."},
                            "evidence_path": "sources/legacy/projects/rooster-old.md",
                            "time_ref": "2026-04-01",
                            "confidence": "high",
                        }
                    ],
                }
            raise AssertionError(schema_name)

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    project_state = load_markdown_document((output_root / "projects" / "rooster" / "state.md").read_text(encoding="utf-8"))

    assert "2026-04-01: Rooster was parked." in project_state.body


def test_migration_engine_replaces_project_state_claims_instead_of_keeping_multiple_active_claims(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    (legacy_root / "memory" / "archive" / "projects").mkdir(parents=True)
    (legacy_root / "memory" / "daily").mkdir(parents=True)
    (legacy_root / "memory" / "archive" / "projects" / "rooster-old.md").write_text(
        "Rooster was parked.\n",
        encoding="utf-8",
    )
    (legacy_root / "memory" / "daily" / "2026-04-13-digest.md").write_text(
        "Rooster is the active focus.\n",
        encoding="utf-8",
    )

    class _FakeClient:
        def generate_json(self, *, schema_name: str, user_prompt: str, **_: object):
            path_hint = user_prompt.splitlines()[1]
            if "archive/projects" in path_hint:
                return {
                    "doc_class": "source_legacy",
                    "canonicality": "evidence",
                    "target_path": "sources/legacy/projects/rooster-old.md",
                    "domain": "work",
                    "entity_refs": ["project:rooster"],
                    "decision_refs": [],
                    "time_scope": "historical",
                    "confidence": "medium",
                    "action": "store_as_source",
                    "reason": "legacy archive",
                    "source_quality": "mixed",
                    "resolution_mode": "resolved",
                    "quarantine_reason": None,
                    "entity_candidates": [
                        {"ref": "project:rooster", "display_name": "Rooster", "aliases": [], "confidence": "medium"}
                    ],
                    "atoms": [
                        {
                            "kind": "project_update",
                            "subject_ref": "project:rooster",
                            "payload": {"title": "Rooster", "summary": "Rooster was parked."},
                            "evidence_path": "sources/legacy/projects/rooster-old.md",
                            "time_ref": "2026-04-01",
                            "confidence": "medium",
                        }
                    ],
                }
            return {
                "doc_class": "digest_daily",
                "canonicality": "evidence",
                "target_path": "digests/daily/2026-04-13-digest.md",
                "domain": "work",
                "entity_refs": ["project:rooster"],
                "decision_refs": [],
                "time_scope": "current",
                "confidence": "high",
                "action": "route_final",
                "reason": "daily digest",
                "source_quality": "strong",
                "resolution_mode": "resolved",
                "quarantine_reason": None,
                "entity_candidates": [
                    {"ref": "project:rooster", "display_name": "Rooster", "aliases": [], "confidence": "high"}
                ],
                "atoms": [
                    {
                        "kind": "project_update",
                        "subject_ref": "project:rooster",
                        "payload": {"title": "Rooster", "summary": "Rooster is the active focus."},
                        "evidence_path": "digests/daily/2026-04-13-digest.md",
                        "time_ref": "2026-04-13",
                        "confidence": "high",
                    }
                ],
            }

    output_root = tmp_path / "corpus"
    MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    from dory_core.claim_store import ClaimStore

    store = ClaimStore(output_root / ".dory" / "claim-store.db")
    current = store.current_claims("project:rooster")
    history = store.claim_history("project:rooster")
    project_state = load_markdown_document((output_root / "projects" / "rooster" / "state.md").read_text(encoding="utf-8"))

    assert len(current) == 1
    assert current[0].statement == "Rooster is the active focus."
    assert any(item.status == "replaced" and item.statement == "Rooster was parked." for item in history)
    assert "## Current State" in project_state.body
    assert "Rooster is the active focus." in project_state.body


def test_migration_engine_reports_progress_events(tmp_path: Path) -> None:
    fixture_root = Path("tests/fixtures/legacy_clawd_brain")
    output_root = tmp_path / "corpus"
    progress_events: list[tuple[str, int]] = []
    migration_events: list[MigrationRunEvent] = []

    MigrationEngine(output_root).migrate(
        fixture_root,
        progress=lambda event: progress_events.append((event.phase, event.percent)),
        events=migration_events.append,
    )

    assert progress_events[0] == ("scan", 0)
    assert progress_events[-1] == ("finalize", 100)
    assert any(phase == "classify" for phase, _ in progress_events)
    assert any(phase == "synthesize" for phase, _ in progress_events)
    assert [event.kind for event in migration_events[:3]] == [
        "scan_started",
        "scan_completed",
        "plan_completed",
    ]
    assert any(event.kind == "file_started" for event in migration_events)
    assert any(event.kind == "file_classified" for event in migration_events)
    assert any(event.kind == "file_extracted" for event in migration_events)
    assert migration_events[-1].kind == "run_completed"


def test_migration_engine_parallel_prepare_uses_multiple_workers(tmp_path: Path) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    for index in range(4):
        (legacy_root / f"note-{index}.md").write_text(f"# Note {index}\n\nRooster {index}\n", encoding="utf-8")

    engine = MigrationEngine(tmp_path / "corpus", concurrency=4)
    active = 0
    peak = 0
    lock = Lock()
    original_classify = engine._classify

    def _classify(rel_path: Path, text: str):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            sleep(0.02)
            return original_classify(rel_path, text)
        finally:
            with lock:
                active -= 1

    engine._classify = _classify  # type: ignore[method-assign]
    result = engine.migrate(legacy_root)

    assert result.staged_count == 4
    assert peak >= 2


def test_migration_engine_uses_stable_per_run_ids_and_unique_rerun_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    note = legacy_root / "legacy.md"
    note.write_text("Imported source.\n", encoding="utf-8")

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

    timestamps = iter(
        (
            datetime(2026, 4, 14, 12, 0, 0, 100000, tzinfo=UTC),
            datetime(2026, 4, 14, 12, 0, 1, 200000, tzinfo=UTC),
            datetime(2026, 4, 14, 12, 0, 2, 300000, tzinfo=UTC),
            datetime(2026, 4, 14, 12, 0, 3, 400000, tzinfo=UTC),
        )
    )

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            value = next(timestamps)
            if tz is None:
                return value.replace(tzinfo=None)
            return value.astimezone(tz)

    monkeypatch.setattr("dory_core.migration_engine.datetime", _FakeDatetime)

    output_root = tmp_path / "corpus"
    first = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)
    second = MigrationEngine(output_root, llm=MigrationLLM(client=_FakeClient())).migrate(legacy_root)

    first_report_stem = Path(first.report_path).stem
    first_artifact_stem = Path(first.run_artifact_path).stem
    second_report_stem = Path(second.report_path).stem
    second_artifact_stem = Path(second.run_artifact_path).stem

    assert first_report_stem == first_artifact_stem
    assert second_report_stem == second_artifact_stem
    assert first_report_stem != second_report_stem
    assert (output_root / first.report_path).exists()
    assert (output_root / first.run_artifact_path).exists()
    assert (output_root / second.report_path).exists()
    assert (output_root / second.run_artifact_path).exists()
