from __future__ import annotations

import json
from pathlib import Path

from dory_cli.main import app


def test_memory_schema_migration_acceptance(cli_runner, tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    legacy_root = Path("tests/fixtures/legacy_clawd_brain")

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "migrate",
            "--no-llm",
            str(legacy_root),
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"staged_count"' in result.output
    assert '"canonical_created_count"' in result.output
    assert '"quarantined_count": 0' in result.output

    assert not (corpus_root / "core" / "user.md").exists()
    assert not (corpus_root / "core" / "soul.md").exists()
    assert not (corpus_root / "people" / "casey.md").exists()
    assert not (corpus_root / "projects" / "rooster" / "state.md").exists()
    assert not (corpus_root / "concepts" / "openclaw.md").exists()
    assert (corpus_root / "sources" / "imported" / "user.md").exists()
    assert (corpus_root / "sources" / "imported" / "soul.md").exists()
    assert (corpus_root / "digests" / "daily" / "2026-03-25-digest.md").exists()
    assert (corpus_root / "logs" / "sessions" / "2026-03-20-revenue-plan.md").exists()
    assert (corpus_root / "references" / "reports" / "migrations").exists()
    assert not (corpus_root / "inbox" / "quarantine").exists()


def test_memory_schema_migration_acceptance_supports_fake_llm_operator_path(
    cli_runner,
    tmp_path: Path,
    monkeypatch,
) -> None:
    corpus_root = tmp_path / "corpus"
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
                            "verdict": "pass",
                            "summary": "Grounded page.",
                            "issues": [],
                        }
                    ]
                }
            raise AssertionError(schema_name)

    monkeypatch.setattr("dory_cli.main.build_openrouter_client", lambda settings=None, purpose=None: _FakeClient())

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "migrate",
            "--llm",
            str(legacy_root),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["staged_count"] == 1
    assert payload["canonical_created_count"] == 1
    assert payload["quarantined_count"] == 0

    person_page = corpus_root / "people" / "casey.md"
    assert person_page.exists()
    assert "Prefers async work." in person_page.read_text(encoding="utf-8")

    run_artifact_path = corpus_root / payload["run_artifact_path"]
    assert run_artifact_path.exists()
    run_artifact = json.loads(run_artifact_path.read_text(encoding="utf-8"))
    assert run_artifact["audit_artifact_path"] == f"inbox/migration-runs/{run_artifact_path.stem}.audit.json"
    assert run_artifact["quarantined_count"] == 0
    assert (corpus_root / run_artifact["audit_artifact_path"]).exists()

    document_artifact = corpus_root / "inbox" / "migration-documents" / run_artifact_path.stem / "profile.json"
    assert document_artifact.exists()
    artifact_payload = json.loads(document_artifact.read_text(encoding="utf-8"))
    assert artifact_payload["used_llm_for_classification"] is True
    assert artifact_payload["used_llm_for_extraction"] is True
    assert artifact_payload["resolution_mode"] == "resolved"


def test_memory_schema_migration_acceptance_records_quarantine_counters_and_artifacts(
    cli_runner,
    tmp_path: Path,
    monkeypatch,
) -> None:
    corpus_root = tmp_path / "corpus"
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "note.md").write_text("# Note\n\nThis import is too ambiguous to promote.\n", encoding="utf-8")

    class _FakeClient:
        def generate_json(self, *, schema_name: str, **_: object):
            if schema_name == "dory_migration_document":
                return {
                    "doc_class": "misc_operational",
                    "canonicality": "canonical",
                    "target_path": "projects/unknown/state.md",
                    "domain": "mixed",
                    "entity_refs": [],
                    "decision_refs": [],
                    "time_scope": "mixed",
                    "confidence": "low",
                    "action": "quarantine",
                    "reason": "ambiguous note",
                    "source_quality": "weak",
                    "resolution_mode": "quarantine",
                    "quarantine_reason": "ambiguous subject and unsupported structure",
                    "entity_candidates": [],
                    "atoms": [],
                }
            raise AssertionError(schema_name)

    monkeypatch.setattr("dory_cli.main.build_openrouter_client", lambda settings=None, purpose=None: _FakeClient())

    result = cli_runner.invoke(
        app,
        [
            "--corpus-root",
            str(corpus_root),
            "migrate",
            "--llm",
            str(legacy_root),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["staged_count"] == 1
    assert payload["canonical_created_count"] == 0
    assert payload["quarantined_count"] == 1

    quarantine_path = corpus_root / "inbox" / "quarantine" / "note.md"
    assert quarantine_path.exists()
    quarantine_text = quarantine_path.read_text(encoding="utf-8")
    assert "Reason: ambiguous subject and unsupported structure" in quarantine_text
    assert "This import is too ambiguous to promote." in quarantine_text

    run_artifact_path = corpus_root / payload["run_artifact_path"]
    run_artifact = json.loads(run_artifact_path.read_text(encoding="utf-8"))
    assert run_artifact["quarantined_count"] == 1
    assert run_artifact["audit_artifact_path"] is None
    assert run_artifact["repair_artifact_path"] is None

    document_artifact = corpus_root / "inbox" / "migration-documents" / run_artifact_path.stem / "note.json"
    assert document_artifact.exists()
    artifact_payload = json.loads(document_artifact.read_text(encoding="utf-8"))
    assert artifact_payload["resolution_mode"] == "quarantine"
    assert artifact_payload["quarantine_reason"] == "ambiguous subject and unsupported structure"

    report_text = (corpus_root / payload["report_path"]).read_text(encoding="utf-8")
    assert "- quarantined_count: 1" in report_text
