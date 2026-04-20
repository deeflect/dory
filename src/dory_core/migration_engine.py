from __future__ import annotations

import csv
import json
import re
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Callable, Iterable

import yaml

from dory_core.canonical_pages import (
    build_timeline_entry,
    canonical_title_from_subject,
    infer_aliases_from_subject,
    patch_canonical_markdown,
    patch_core_markdown,
    render_canonical_from_claims,
)
from dory_core.claim_store import ClaimStore
from dory_core.claim_store import ClaimRecord
from dory_core.entity_registry import EntityRecord, EntityRegistry
from dory_core.fs import atomic_write_text
from dory_core.frontmatter import dump_markdown_document, load_markdown_document
from dory_core.migration_events import MigrationEventKind, MigrationRunEvent
from dory_core.migration_llm import MigrationLLM
from dory_core.migration_normalize import (
    canonical_target_for_subject,
    concept_kind_for_legacy_path,
    normalize_classification_target,
    normalize_migration_slug,
    render_canonical_template,
    render_core_template,
)
from dory_core.migration_resolve import build_contradiction_record, choose_winning_atom, route_by_confidence
from dory_core.migration_types import (
    ClassifiedDocument,
    MemoryAtom,
    MigrationEntityCandidate,
    MigrationEntityMention,
    MigrationPageAudit,
    MigrationPageRepair,
)

_SUBJECT_LABEL_RE = re.compile(r"^\s*[-*]?\s*(name|title|subject|project|decision)\s*:\s*(?P<value>.+?)\s*$", re.I)
_HEADING_RE = re.compile(r"^\s*#+\s*(?P<value>.+?)\s*$")
_GENERIC_SUBJECT_HEADINGS = {
    "document",
    "daily",
    "daily digest",
    "concept",
    "decision",
    "digest",
    "project",
    "notes",
    "note",
    "overview",
    "profile",
    "summary",
    "spec",
    "subject",
    "weekly",
    "weekly digest",
    "state",
    "user",
}
_IGNORED_STAGE_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".turbo",
    "coverage",
    "__pycache__",
    ".pytest_cache",
}
_SUPPORTED_STAGE_SUFFIXES = {".md", ".json", ".jsonl", ".ndjson", ".txt", ".yaml", ".yml", ".toml", ".csv"}
_STRUCTURED_DATE_KEYS = (
    "time_ref",
    "date",
    "created",
    "created_at",
    "occurred_at",
    "updated",
    "updated_at",
    "first_timestamp",
    "timestamp",
)


@dataclass(frozen=True, slots=True)
class MigrationStats:
    llm_classified_count: int = 0
    llm_extracted_count: int = 0
    fallback_classified_count: int = 0
    fallback_extracted_count: int = 0
    atom_count: int = 0
    contradiction_count: int = 0
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class MigrationProgress:
    phase: str
    percent: int
    processed_count: int
    total_count: int
    path: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class MigrationRun:
    staged_count: int
    written_count: int
    canonical_created_count: int
    quarantined_count: int
    report_path: str
    run_artifact_path: str
    stats: MigrationStats


@dataclass(frozen=True, slots=True)
class MigrationFallbackWarning:
    stage: str
    message: str
    scope: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "stage": self.stage,
            "scope": self.scope,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class PreparedMigrationDocument:
    index: int
    rel_path: Path
    text: str
    classified: ClassifiedDocument
    used_llm_for_classification: bool
    extracted_atoms: tuple[MemoryAtom, ...]
    used_llm_for_extraction: bool
    resolution_mode: str
    entity_candidates: tuple[MigrationEntityCandidate, ...] = ()
    quarantine_reason: str | None = None
    fallback_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedMigrationDocument:
    rel_path: Path
    classified: ClassifiedDocument
    evidence_target: Path
    atoms: tuple[MemoryAtom, ...]
    entity_candidates: tuple[MigrationEntityCandidate, ...]
    entity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StructuredSourcePromotion:
    adapter_name: str
    atom: MemoryAtom


class MigrationEngine:
    def __init__(self, output_root: Path, *, llm: MigrationLLM | None = None, concurrency: int = 1) -> None:
        self.output_root = Path(output_root)
        self.llm = llm
        self.concurrency = max(1, concurrency)

    def migrate(
        self,
        legacy_root: Path,
        *,
        progress: Callable[[MigrationProgress], None] | None = None,
        events: Callable[[MigrationRunEvent], None] | None = None,
        selected_paths: Iterable[Path] | None = None,
    ) -> MigrationRun:
        started_at = perf_counter()
        legacy_root = Path(legacy_root).resolve()
        self._emit_progress(progress, phase="scan", percent=0, processed=0, total=0, message="Scanning legacy corpus")
        staged: list[Path] = []
        written_paths: set[Path] = set()
        canonical_written: set[Path] = set()
        canonical_entity_ids_by_path: dict[Path, str] = {}
        quarantined = 0
        alias_map: dict[str, str] = {}
        subject_aliases: dict[str, set[str]] = {}
        registry = EntityRegistry(self.output_root / ".dory" / "entity-registry.db")
        claim_store = ClaimStore(self.output_root / ".dory" / "claim-store.db")
        atoms: list[MemoryAtom] = []
        llm_classified_count = 0
        llm_extracted_count = 0
        fallback_classified_count = 0
        fallback_extracted_count = 0
        total_staged = 0
        current_phase = "scan"
        current_path: str | None = None
        run_id = self._run_id(legacy_root)
        resolved_documents: list[ResolvedMigrationDocument] = []
        fallback_warnings: list[MigrationFallbackWarning] = []

        try:
            self._emit_event(
                events,
                kind="scan_started",
                phase="scan",
                processed=0,
                total=0,
                message="Scanning legacy corpus",
                llm_classified_count=llm_classified_count,
                llm_extracted_count=llm_extracted_count,
                fallback_classified_count=fallback_classified_count,
                fallback_extracted_count=fallback_extracted_count,
                atom_count=len(atoms),
                canonical_created_count=len(canonical_written),
                written_count=len(written_paths),
                quarantined_count=quarantined,
                contradiction_count=0,
            )
            staged = self._resolve_staged_paths(legacy_root, selected_paths)
            total_staged = len(staged)
            self.output_root.mkdir(parents=True, exist_ok=True)
            self._emit_event(
                events,
                kind="scan_completed",
                phase="scan",
                processed=total_staged,
                total=total_staged,
                message=f"Found {total_staged} legacy files",
                llm_classified_count=llm_classified_count,
                llm_extracted_count=llm_extracted_count,
                fallback_classified_count=fallback_classified_count,
                fallback_extracted_count=fallback_extracted_count,
                atom_count=len(atoms),
                canonical_created_count=len(canonical_written),
                written_count=len(written_paths),
                quarantined_count=quarantined,
                contradiction_count=0,
            )
            self._emit_event(
                events,
                kind="plan_completed",
                phase="plan",
                processed=total_staged,
                total=total_staged,
                message=f"Prepared migration plan for {total_staged} markdown files",
                llm_classified_count=llm_classified_count,
                llm_extracted_count=llm_extracted_count,
                fallback_classified_count=fallback_classified_count,
                fallback_extracted_count=fallback_extracted_count,
                atom_count=len(atoms),
                canonical_created_count=len(canonical_written),
                written_count=len(written_paths),
                quarantined_count=quarantined,
                contradiction_count=0,
            )

            current_phase = "classify"
            self._emit_progress(
                progress,
                phase="classify",
                percent=5,
                processed=0,
                total=total_staged,
                message=f"Found {total_staged} legacy files",
            )

            prepared_documents = self._prepare_documents(
                staged,
                legacy_root=legacy_root,
                progress=progress,
            )
            for prepared in prepared_documents:
                index = prepared.index
                rel = prepared.rel_path
                current_path = rel.as_posix()
                classified = prepared.classified
                text = prepared.text

                self._emit_event(
                    events,
                    kind="file_started",
                    phase="classify",
                    processed=index - 1,
                    total=total_staged,
                    path=current_path,
                    message=f"Starting {current_path}",
                    llm_classified_count=llm_classified_count,
                    llm_extracted_count=llm_extracted_count,
                    fallback_classified_count=fallback_classified_count,
                    fallback_extracted_count=fallback_extracted_count,
                    atom_count=len(atoms),
                    canonical_created_count=len(canonical_written),
                    written_count=len(written_paths),
                    quarantined_count=quarantined,
                    contradiction_count=0,
                )

                if prepared.used_llm_for_classification:
                    llm_classified_count += 1
                else:
                    fallback_classified_count += 1
                if prepared.used_llm_for_extraction:
                    llm_extracted_count += 1
                else:
                    fallback_extracted_count += 1

                artifact_target = self._write_document_artifact(run_id=run_id, prepared=prepared)
                written_paths.add(artifact_target)
                resolved_atoms = tuple(self._resolve_atom_alias(atom, alias_map) for atom in prepared.extracted_atoms)
                self._emit_event(
                    events,
                    kind="file_classified",
                    phase="classify",
                    processed=index,
                    total=total_staged,
                    path=current_path,
                    message=classified.reason,
                    llm_classified_count=llm_classified_count,
                    llm_extracted_count=llm_extracted_count,
                    fallback_classified_count=fallback_classified_count,
                    fallback_extracted_count=fallback_extracted_count,
                    atom_count=len(atoms),
                    canonical_created_count=len(canonical_written),
                    written_count=len(written_paths),
                    quarantined_count=quarantined,
                    contradiction_count=0,
                )
                evidence_target = self._effective_evidence_target(rel, classified)
                resolved_atoms = tuple(
                    MemoryAtom(
                        kind=atom.kind,
                        subject_ref=atom.subject_ref,
                        payload=dict(atom.payload),
                        evidence_path=evidence_target.as_posix(),
                        time_ref=atom.time_ref,
                        confidence=atom.confidence,
                    )
                    for atom in resolved_atoms
                )
                route = route_by_confidence(classified.confidence, canonicality=classified.canonicality)
                if prepared.resolution_mode == "quarantine":
                    route = "quarantine"
                if route == "quarantine":
                    quarantine_target = self._write_quarantine(rel, text, prepared.quarantine_reason or classified.reason)
                    written_paths.add(quarantine_target)
                    quarantined += 1
                    self._emit_event(
                        events,
                        kind="file_quarantined",
                        phase="classify",
                        processed=index,
                        total=total_staged,
                        path=current_path,
                        message="Quarantined low-confidence document",
                        llm_classified_count=llm_classified_count,
                        llm_extracted_count=llm_extracted_count,
                        fallback_classified_count=fallback_classified_count,
                        fallback_extracted_count=fallback_extracted_count,
                        atom_count=len(atoms),
                        canonical_created_count=len(canonical_written),
                        written_count=len(written_paths),
                        quarantined_count=quarantined,
                        contradiction_count=0,
                    )
                    self._emit_progress(
                        progress,
                        phase="classify",
                        percent=self._phase_percent(index, total_staged, start=5, end=75),
                        processed=index,
                        total=total_staged,
                        path=current_path,
                        message="Quarantined low-confidence document",
                    )
                    continue

                written_paths.add(self._write_evidence(evidence_target, text, classified))

                if prepared.resolution_mode != "resolved":
                    self._emit_event(
                        events,
                        kind="file_extracted",
                        phase="classify",
                        processed=index,
                        total=total_staged,
                        path=current_path,
                        message="Stored evidence without semantic promotion",
                        llm_classified_count=llm_classified_count,
                        llm_extracted_count=llm_extracted_count,
                        fallback_classified_count=fallback_classified_count,
                        fallback_extracted_count=fallback_extracted_count,
                        atom_count=len(atoms),
                        canonical_created_count=len(canonical_written),
                        written_count=len(written_paths),
                        quarantined_count=quarantined,
                        contradiction_count=0,
                    )
                    self._emit_progress(
                        progress,
                        phase="classify",
                        percent=self._phase_percent(index, total_staged, start=5, end=75),
                        processed=index,
                        total=total_staged,
                        path=current_path,
                        message=f"Stored evidence for {rel.as_posix()}",
                    )
                    continue

                if classified.canonicality == "canonical" and classified.doc_class.startswith("core_"):
                    target = self._write_core(classified, text, evidence_path=evidence_target)
                    written_paths.add(target)
                    canonical_written.add(target)

                resolved_documents.append(
                    ResolvedMigrationDocument(
                        rel_path=rel,
                        classified=classified,
                        evidence_target=evidence_target,
                        atoms=resolved_atoms,
                        entity_candidates=prepared.entity_candidates,
                        entity_ids=(),
                    )
                )
                self._emit_event(
                    events,
                    kind="file_extracted",
                    phase="classify",
                    processed=index,
                    total=total_staged,
                    path=current_path,
                    message=f"Extracted {len(resolved_atoms)} atoms",
                    llm_classified_count=llm_classified_count,
                    llm_extracted_count=llm_extracted_count,
                    fallback_classified_count=fallback_classified_count,
                    fallback_extracted_count=fallback_extracted_count,
                    atom_count=len(atoms),
                    canonical_created_count=len(canonical_written),
                    written_count=len(written_paths),
                    quarantined_count=quarantined,
                    contradiction_count=0,
                )
                self._emit_progress(
                    progress,
                    phase="classify",
                    percent=self._phase_percent(index, total_staged, start=5, end=75),
                    processed=index,
                    total=total_staged,
                    path=current_path,
                    message=f"Processed {rel.as_posix()}",
                )

            current_phase = "synthesize"
            contradictions: list[dict[str, str]] = []
            resolved_documents = self._resolve_documents(
                resolved_documents,
                registry=registry,
                claim_store=claim_store,
                alias_map=alias_map,
                subject_aliases=subject_aliases,
                fallback_warnings=fallback_warnings,
            )
            grouped_atoms = self._group_atoms(
                atom for document in resolved_documents for atom in document.atoms
            )
            atoms = [atom for document in resolved_documents for atom in document.atoms]
            total_subjects = len({entity_id for document in resolved_documents for entity_id in document.entity_ids})
            self._emit_progress(
                progress,
                phase="synthesize",
                percent=76,
                processed=0,
                total=total_subjects,
                message=f"Synthesizing {total_subjects} canonical subjects",
            )
            resolved_entity_ids = tuple(
                entity_id
                for entity_id in self._dedupe_preserve_order(
                    entity_id for document in resolved_documents for entity_id in document.entity_ids
                )
            )
            contradictions.extend(self._collect_contradictions(grouped_atoms))
            for index, entity_id in enumerate(resolved_entity_ids, start=1):
                target = self._write_canonical_subject_from_store(
                    entity_id,
                    registry=registry,
                    claim_store=claim_store,
                    subject_aliases=subject_aliases,
                )
                if target is not None:
                    written_paths.add(target)
                    canonical_written.add(target)
                    canonical_entity_ids_by_path[target] = entity_id
                self._emit_event(
                    events,
                    kind="subject_synthesized",
                    phase="synthesize",
                    processed=index,
                    total=total_subjects,
                    path=entity_id,
                    message=f"Synthesized {entity_id}",
                    llm_classified_count=llm_classified_count,
                    llm_extracted_count=llm_extracted_count,
                    fallback_classified_count=fallback_classified_count,
                    fallback_extracted_count=fallback_extracted_count,
                    atom_count=len(atoms),
                    canonical_created_count=len(canonical_written),
                    written_count=len(written_paths),
                    quarantined_count=quarantined,
                    contradiction_count=len(contradictions),
                )
                self._emit_progress(
                    progress,
                    phase="synthesize",
                    percent=self._phase_percent(index, total_subjects, start=76, end=95),
                    processed=index,
                    total=total_subjects,
                    path=entity_id,
                    message=f"Synthesized {entity_id}",
                )

            current_phase = "finalize"
            audits = self._audit_generated_pages(tuple(canonical_written), fallback_warnings=fallback_warnings)
            repairs = self._repair_generated_pages(
                tuple(canonical_written),
                audits=audits,
                entity_ids_by_path=canonical_entity_ids_by_path,
                claim_store=claim_store,
                fallback_warnings=fallback_warnings,
            )
            if repairs:
                self._apply_page_repairs(repairs)
                refreshed_audits = self._audit_generated_pages(
                    tuple(canonical_written),
                    fallback_warnings=fallback_warnings,
                )
                if refreshed_audits:
                    audits = refreshed_audits
            repair_artifact_path = self._write_repair_artifact(run_id=run_id, repairs=repairs) if repairs else None
            audit_artifact_path = self._write_audit_artifact(run_id=run_id, audits=audits) if audits else None
            report_path = self._write_report(
                run_id=run_id,
                staged_count=len(staged),
                written_count=len(written_paths),
                canonical_created_count=len(canonical_written),
                quarantined_count=quarantined,
                contradictions=contradictions,
                audits=audits,
                repairs=repairs,
                audit_artifact_path=audit_artifact_path,
                repair_artifact_path=repair_artifact_path,
                fallback_warnings=tuple(fallback_warnings),
            )
            run_artifact_path = self._write_run_artifact(
                run_id=run_id,
                legacy_root=legacy_root,
                staged_count=len(staged),
                written_count=len(written_paths),
                canonical_created_count=len(canonical_written),
                quarantined_count=quarantined,
                contradictions=contradictions,
                aliases=alias_map,
                audits=audits,
                repairs=repairs,
                audit_artifact_path=audit_artifact_path,
                repair_artifact_path=repair_artifact_path,
                fallback_warnings=tuple(fallback_warnings),
            )
            duration_ms = int((perf_counter() - started_at) * 1000)
            stats = MigrationStats(
                llm_classified_count=llm_classified_count,
                llm_extracted_count=llm_extracted_count,
                fallback_classified_count=fallback_classified_count,
                fallback_extracted_count=fallback_extracted_count,
                atom_count=len(atoms),
                contradiction_count=len(contradictions),
                duration_ms=duration_ms,
            )
            self._emit_progress(
                progress,
                phase="finalize",
                percent=100,
                processed=1,
                total=1,
                message="Migration complete",
            )
            self._emit_event(
                events,
                kind="run_completed",
                phase="finalize",
                processed=1,
                total=1,
                message="Migration complete",
                llm_classified_count=llm_classified_count,
                llm_extracted_count=llm_extracted_count,
                fallback_classified_count=fallback_classified_count,
                fallback_extracted_count=fallback_extracted_count,
                atom_count=len(atoms),
                canonical_created_count=len(canonical_written),
                written_count=len(written_paths),
                quarantined_count=quarantined,
                contradiction_count=len(contradictions),
            )

            return MigrationRun(
                staged_count=len(staged),
                written_count=len(written_paths),
                canonical_created_count=len(canonical_written),
                quarantined_count=quarantined,
                report_path=report_path.as_posix(),
                run_artifact_path=run_artifact_path.as_posix(),
                stats=stats,
            )
        except Exception as exc:
            self._emit_event(
                events,
                kind="run_failed",
                phase=current_phase,
                processed=len(staged),
                total=total_staged,
                path=current_path,
                message=f"{type(exc).__name__}: {exc}",
                llm_classified_count=llm_classified_count,
                llm_extracted_count=llm_extracted_count,
                fallback_classified_count=fallback_classified_count,
                fallback_extracted_count=fallback_extracted_count,
                atom_count=len(atoms),
                canonical_created_count=len(canonical_written),
                written_count=len(written_paths),
                quarantined_count=quarantined,
                contradiction_count=0,
            )
            raise

    def _resolve_staged_paths(self, legacy_root: Path, selected_paths: Iterable[Path] | None) -> list[Path]:
        if selected_paths is None:
            return sorted(
                path.resolve()
                for path in legacy_root.rglob("*")
                if path.is_file()
                and self._is_supported_stage_path(path)
                and self._should_stage_path(path.relative_to(legacy_root))
            )

        staged: list[Path] = []
        seen: set[Path] = set()
        for selected in selected_paths:
            selected_path = Path(selected)
            if not selected_path.is_absolute():
                direct_candidate = selected_path.resolve()
                rooted_candidate = (legacy_root / selected_path).resolve()
                selected_path = direct_candidate if direct_candidate.is_file() else rooted_candidate
            else:
                selected_path = selected_path.resolve()
            if not self._is_supported_stage_path(selected_path) or not selected_path.is_file():
                continue
            try:
                rel_path = selected_path.relative_to(legacy_root)
            except ValueError:
                continue
            if not self._should_stage_path(rel_path):
                continue
            if selected_path in seen:
                continue
            seen.add(selected_path)
            staged.append(selected_path)
        staged.sort()
        return staged

    def migrate_path(self, legacy_root: Path) -> MigrationRun:
        return self.migrate(legacy_root)

    def _prepare_documents(
        self,
        staged: list[Path],
        *,
        legacy_root: Path,
        progress: Callable[[MigrationProgress], None] | None,
    ) -> list[PreparedMigrationDocument]:
        if not staged:
            return []
        if self.concurrency <= 1 or len(staged) <= 1:
            return [
                self._prepare_document(index=index, path=path, legacy_root=legacy_root)
                for index, path in enumerate(staged, start=1)
            ]

        prepared: dict[int, PreparedMigrationDocument] = {}
        with ThreadPoolExecutor(max_workers=min(self.concurrency, len(staged))) as executor:
            futures = {
                executor.submit(
                    self._prepare_document,
                    index=index,
                    path=path,
                    legacy_root=legacy_root,
                ): index
                for index, path in enumerate(staged, start=1)
            }
            for future in as_completed(futures):
                document = future.result()
                prepared[document.index] = document
                self._emit_progress(
                    progress,
                    phase="classify",
                    percent=self._phase_percent(len(prepared), len(staged), start=5, end=75),
                    processed=len(prepared),
                    total=len(staged),
                    path=document.rel_path.as_posix(),
                    message=f"Prepared {document.rel_path.as_posix()}",
                )
        return [prepared[index] for index in sorted(prepared)]

    def _prepare_document(
        self,
        *,
        index: int,
        path: Path,
        legacy_root: Path,
    ) -> PreparedMigrationDocument:
        rel = path.relative_to(legacy_root)
        text = self._load_legacy_text(path, rel_path=rel)
        fallback_reasons: list[str] = []
        if self.llm is not None:
            try:
                extracted = self.llm.extract_document(path=rel.as_posix(), text=text)
                classified = normalize_classification_target(extracted.classified)
                return PreparedMigrationDocument(
                    index=index,
                    rel_path=rel,
                    text=text,
                    classified=classified,
                    used_llm_for_classification=True,
                    extracted_atoms=extracted.atoms,
                    used_llm_for_extraction=True,
                    resolution_mode=extracted.resolution_mode,
                    entity_candidates=extracted.entity_candidates,
                    quarantine_reason=extracted.quarantine_reason,
                )
            except Exception as err:
                fallback_reasons.append(f"document_extraction_failed: {self._format_exception(err)}")

        classified, used_llm_for_classification, classification_fallback_reason = self._classify(rel, text)
        if classification_fallback_reason is not None:
            fallback_reasons.append(classification_fallback_reason)
        extracted_atoms = self._deterministic_prepared_atoms(classified, text)
        resolution_mode = self._fallback_resolution_mode(classified, extracted_atoms, text=text)
        return PreparedMigrationDocument(
            index=index,
            rel_path=rel,
            text=text,
            classified=classified,
            used_llm_for_classification=used_llm_for_classification,
            extracted_atoms=extracted_atoms,
            used_llm_for_extraction=False,
            resolution_mode=resolution_mode,
            entity_candidates=(),
            fallback_reasons=tuple(fallback_reasons),
        )

    def _classify(self, rel_path: Path, text: str) -> tuple[ClassifiedDocument, bool, str | None]:
        path = rel_path.as_posix()
        if self.llm is not None:
            try:
                classified = self.llm.classify_document(path=path, text=text)
                if classified.target_path.strip():
                    return normalize_classification_target(classified), True, None
            except Exception as err:
                return (
                    normalize_classification_target(self._classify_deterministic(rel_path, text)),
                    False,
                    f"classification_failed: {self._format_exception(err)}",
                )
        return normalize_classification_target(self._classify_deterministic(rel_path, text)), False, None

    def _classify_deterministic(self, rel_path: Path, text: str) -> ClassifiedDocument:
        path = rel_path.as_posix()
        stem = rel_path.stem.lower()
        root_name = rel_path.parts[0] if rel_path.parts else ""
        root_target_name = self._markdown_target_name(rel_path.name)

        transcript_target = self._session_target_for_structured_transcript(rel_path, text)
        if transcript_target is not None:
            return self._classified("session_log", transcript_target, path)

        if root_name == "memory":
            bucket = rel_path.parts[1] if len(rel_path.parts) > 1 else ""
            tail = Path(*rel_path.parts[2:]) if len(rel_path.parts) > 2 else Path(rel_path.name)
            tail_name = self._markdown_target_name(tail.name)
            if bucket == "daily":
                return self._classified("digest_daily", f"digests/daily/{tail_name}", path)
            if bucket == "weekly":
                return self._classified("digest_weekly", f"digests/weekly/{tail_name}", path)
            if bucket == "sessions":
                return self._classified("session_log", f"logs/sessions/{tail_name}", path)
            if bucket == "tools":
                return self._classified("concept_note", f"sources/imported/tools/{tail_name}", path)
            if bucket == "projects":
                doc_class = "project_spec" if "spec" in stem else "project_state"
                return self._classified(doc_class, f"sources/imported/projects/{tail_name}", path)
            if bucket == "people":
                return self._classified("person_profile", f"sources/imported/people/{tail_name}", path)
            if bucket == "archive":
                tail = Path(*rel_path.parts[2:]) if len(rel_path.parts) > 2 else Path(rel_path.name)
                tail_rel = tail.with_name(self._markdown_target_name(tail.name))
                return self._classified("source_legacy", f"sources/legacy/{tail_rel.as_posix()}", path)

        if rel_path.parts and len(rel_path.parts) == 1:
            if rel_path.suffix.lower() == ".md":
                return self._classified("source_imported", f"sources/imported/{root_target_name}", path)
            return self._classified("source_imported", f"sources/imported/root/{root_target_name}", path)

        rel_target = self._markdown_rel_path(rel_path)
        return self._classified("source_imported", f"sources/imported/{rel_target.as_posix()}", path)

    def _classified(
        self,
        doc_class: str,
        target_path: str,
        path: str,
        *,
        entity_refs: tuple[str, ...] = (),
    ) -> ClassifiedDocument:
        return ClassifiedDocument(
            doc_class=doc_class,
            canonicality="evidence",
            target_path=target_path,
            domain="mixed",
            entity_refs=entity_refs,
            decision_refs=(),
            time_scope="mixed",
            confidence="medium",
            action="store_as_source",
            reason=f"deterministic migration mapping from {path}",
        )

    def _extract_atoms(
        self,
        rel_path: Path,
        classified: ClassifiedDocument,
        text: str,
        alias_map: dict[str, str],
    ) -> tuple[tuple[MemoryAtom, ...], bool]:
        if self.llm is not None:
            try:
                llm_atoms = self.llm.extract_atoms(path=rel_path.as_posix(), text=text, classified=classified)
                if llm_atoms:
                    return tuple(self._resolve_atom_alias(atom, alias_map) for atom in llm_atoms), True
            except Exception:
                pass
        return tuple(self._resolve_atom_alias(atom, alias_map) for atom in self._extract_atoms_deterministic(classified, text)), False

    def _should_stage_path(self, rel_path: Path) -> bool:
        return not any(part in _IGNORED_STAGE_PARTS for part in rel_path.parts)

    def _extract_atoms_deterministic(self, classified: ClassifiedDocument, text: str) -> tuple[MemoryAtom, ...]:
        body = self._document_body(text)
        evidence_path = classified.target_path
        time_ref = self._structured_date(text) or self._extract_time_ref(evidence_path)

        subject_refs = self._deterministic_subject_refs(classified, body)
        atoms: list[MemoryAtom] = []
        for subject_ref in subject_refs:
            family = self._family_from_subject(subject_ref)
            summary = self._summary_from_body(body, family=family)
            payload: dict[str, object] = {"summary": summary}
            title = self._title_from_body(body, family=family)
            if title is not None:
                payload["title"] = title
            if family == "concept":
                payload["concept_kind"] = concept_kind_for_legacy_path(classified.target_path)
            atoms.append(
                MemoryAtom(
                    kind=self._kind_for_family(family),
                    subject_ref=subject_ref,
                    payload=payload,
                    evidence_path=evidence_path,
                    time_ref=time_ref,
                    confidence="high",
                )
            )

        return tuple(atoms)

    def _deterministic_prepared_atoms(self, classified: ClassifiedDocument, text: str) -> tuple[MemoryAtom, ...]:
        if classified.doc_class == "session_log":
            transcript_atoms = self._extract_session_transcript_atoms(classified, text)
            if transcript_atoms:
                return transcript_atoms
        structured_promotion = self._structured_source_promotion(classified, text)
        if structured_promotion is not None:
            return (structured_promotion.atom,)
        return ()

    def _fallback_resolution_mode(
        self,
        classified: ClassifiedDocument,
        atoms: tuple[MemoryAtom, ...],
        *,
        text: str,
    ) -> str:
        if atoms and classified.doc_class == "session_log":
            return "resolved"
        if atoms and self._structured_source_promotion(classified, text) is not None:
            return "resolved"
        return "evidence_only"

    def _structured_source_promotion(
        self,
        classified: ClassifiedDocument,
        text: str,
    ) -> StructuredSourcePromotion | None:
        payload = self._structured_json_payload(text)
        if not isinstance(payload, dict):
            return None

        schema_promotion = self._schema_structured_promotion(classified, payload, text=text)
        if schema_promotion is not None:
            return schema_promotion
        return self._typed_structured_promotion(classified, payload, text=text)

    def _typed_structured_promotion(
        self,
        classified: ClassifiedDocument,
        payload: dict[str, object],
        *,
        text: str,
    ) -> StructuredSourcePromotion | None:
        family = self._typed_structured_json_family(payload)
        if family is None:
            return None
        title = self._structured_json_title(payload)
        summary = self._structured_json_summary(payload, family=family, title=title)
        if title is None or summary is None:
            return None
        return StructuredSourcePromotion(
            adapter_name="typed_entity_family_fields",
            atom=self._structured_atom_for_family(
                family=family,
                title=title,
                summary=summary,
                evidence_path=classified.target_path,
                time_ref=self._structured_date(text),
            ),
        )

    def _schema_structured_promotion(
        self,
        classified: ClassifiedDocument,
        payload: dict[str, object],
        *,
        text: str,
    ) -> StructuredSourcePromotion | None:
        schema_name = self._structured_schema_name(payload)
        if schema_name is None:
            return None
        family = self._structured_schema_family(schema_name)
        if family is None:
            return None
        title = self._schema_structured_title(payload, family=family)
        summary = self._schema_structured_summary(payload, family=family, title=title)
        if title is None or summary is None:
            return None
        return StructuredSourcePromotion(
            adapter_name=schema_name,
            atom=self._structured_atom_for_family(
                family=family,
                title=title,
                summary=summary,
                evidence_path=classified.target_path,
                time_ref=self._structured_date(text),
            ),
        )

    def _structured_atom_for_family(
        self,
        *,
        family: str,
        title: str,
        summary: str,
        evidence_path: str,
        time_ref: str | None,
    ) -> MemoryAtom:
        kind = {
            "project": "project_update",
            "person": "person_fact",
            "concept": "concept_claim",
            "decision": "decision",
        }[family]
        return MemoryAtom(
            kind=kind,
            subject_ref=f"{family}:{normalize_migration_slug(title)}",
            payload={"title": title, "summary": summary},
            evidence_path=evidence_path,
            time_ref=time_ref,
            confidence="medium",
        )

    def _structured_schema_name(self, payload: dict[str, object]) -> str | None:
        value = payload.get("schema")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _structured_schema_family(self, schema_name: str) -> str | None:
        return {
            "dory.project_export.v1": "project",
            "dory.person_export.v1": "person",
            "dory.decision_export.v1": "decision",
            "dory.concept_export.v1": "concept",
        }.get(schema_name)

    def _schema_structured_title(self, payload: dict[str, object], *, family: str) -> str | None:
        scoped = payload.get(family)
        if isinstance(scoped, dict):
            title = self._first_nonempty_string(scoped, ("title", "name", "subject", "decision"))
            if title is not None:
                return title
        entity = payload.get("entity")
        if isinstance(entity, dict):
            title = self._first_nonempty_string(entity, ("title", "name"))
            if title is not None:
                return title
        return self._first_nonempty_string(payload, ("title", "name", "subject", "decision"))

    def _schema_structured_summary(
        self,
        payload: dict[str, object],
        *,
        family: str,
        title: str | None,
    ) -> str | None:
        if title is None:
            return None
        scoped = payload.get(family)
        if isinstance(scoped, dict):
            summary = self._first_nonempty_string(scoped, ("summary", "description", "statement", "definition", "bio"))
            if summary is not None:
                return summary
        current_state = payload.get("current_state")
        if isinstance(current_state, dict):
            summary = self._first_nonempty_string(current_state, ("summary", "description"))
            if summary is not None:
                return summary
            status = self._first_nonempty_string(current_state, ("status",))
            if status is not None and family == "project":
                return f"{title} is {status}."
        summary = self._first_nonempty_string(payload, ("summary", "description", "decision", "definition", "bio"))
        if summary is not None:
            return summary
        status = self._first_nonempty_string(payload, ("status",))
        if status is not None and family == "project":
            return f"{title} is {status}."
        return None

    def _first_nonempty_string(self, payload: dict[str, object], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _structured_json_payload(self, text: str) -> object | None:
        marker = "## Raw JSON"
        if marker not in text:
            return None
        _, after = text.split(marker, 1)
        if "```json" not in after:
            return None
        _, code_block = after.split("```json", 1)
        if "```" not in code_block:
            return None
        raw_payload, _ = code_block.split("```", 1)
        try:
            return json.loads(raw_payload.strip())
        except json.JSONDecodeError:
            return None

    def _typed_structured_json_family(self, payload: dict[str, object]) -> str | None:
        raw_family = payload.get("kind")
        if not isinstance(raw_family, str) or not raw_family.strip():
            raw_family = payload.get("type")
        if not isinstance(raw_family, str) or not raw_family.strip():
            return None
        normalized = raw_family.strip().lower()
        return normalized if normalized in {"project", "person", "concept", "decision"} else None

    def _structured_json_title(self, payload: dict[str, object]) -> str | None:
        for key in ("title", "name", "subject", "decision"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _structured_json_summary(self, payload: dict[str, object], *, family: str, title: str) -> str | None:
        for key in ("summary", "description", "decision"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        status = payload.get("status")
        if isinstance(status, str) and status.strip() and family == "project":
            return f"{title} is {status.strip()}."
        return None

    def _extract_session_transcript_atoms(self, classified: ClassifiedDocument, text: str) -> tuple[MemoryAtom, ...]:
        transcript = self._transcript_lines(text)
        if not transcript:
            return ()
        evidence_path = classified.target_path
        time_ref = self._structured_date(text) or self._extract_time_ref(evidence_path)
        atoms: list[MemoryAtom] = []
        seen: set[tuple[str, str]] = set()
        for speaker, utterance in transcript:
            if speaker != "assistant":
                continue
            subject_text = self._subject_text_from_sentence(utterance)
            if subject_text is None:
                continue
            subject_ref = f"project:{normalize_migration_slug(subject_text)}"
            if not subject_ref.endswith(":"):
                key = (subject_ref, self._normalize_semantic_text(utterance))
                if key in seen:
                    continue
                seen.add(key)
                atoms.append(
                    MemoryAtom(
                        kind="project_update",
                        subject_ref=subject_ref,
                        payload={"title": subject_text, "summary": utterance.strip()},
                        evidence_path=evidence_path,
                        time_ref=time_ref,
                        confidence="medium",
                    )
                )
        return tuple(atoms)

    def _effective_evidence_target(self, rel_path: Path, classified: ClassifiedDocument) -> Path:
        target_rel = Path(classified.target_path)
        if classified.canonicality != "canonical":
            return target_rel
        rel_text = rel_path.as_posix()
        if classified.doc_class.startswith("core_"):
            if rel_path.parts and len(rel_path.parts) == 1:
                return Path("sources/imported/root") / self._markdown_target_name(rel_path.name)
            return Path("sources/imported") / self._markdown_rel_path(rel_path)
        if rel_path.parts and len(rel_path.parts) == 1:
            return Path("sources/imported/root") / self._markdown_target_name(rel_path.name)
        return Path("sources/imported") / self._markdown_rel_path(rel_path)

    def _write_core(self, classified: ClassifiedDocument, text: str, *, evidence_path: Path) -> Path:
        target_rel = Path(classified.target_path)
        title = "User" if target_rel.stem == "user" else target_rel.stem.replace("-", " ").title()
        summary_text = self._extract_heading_block(
            self._document_body(text),
            default="Legacy root document migrated into core memory.",
        ).strip()
        time_ref = self._structured_date(text) or self._extract_time_ref(evidence_path.as_posix())
        section_name = {
            "user": "Summary",
            "identity": "Role",
            "soul": "Voice",
            "env": "Environment",
            "active": "Current Focus",
            "defaults": "Default Operating Assumptions",
        }.get(target_rel.stem, "Summary")
        update = patch_core_markdown(
            None,
            file_name=target_rel.name,
            title=title,
            aliases=(),
            section_updates={section_name: summary_text},
            timeline_entries=(build_timeline_entry(time_ref=time_ref, summary=summary_text, evidence_path=evidence_path.as_posix()),),
            evidence_paths=(evidence_path.as_posix(),),
        )
        target = self.output_root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, update.body, encoding="utf-8")
        return target_rel

    def _write_evidence(self, target_rel: Path, text: str, classified: ClassifiedDocument) -> Path:
        target = self.output_root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        normalized_type = self._normalized_doc_type(classified.doc_class)
        try:
            document = load_markdown_document(text)
        except ValueError:
            wrapped = dump_markdown_document(
                self._evidence_frontmatter(target_rel=target_rel, classified=classified, normalized_type=normalized_type),
                text,
            )
            atomic_write_text(target, wrapped, encoding="utf-8")
            return target_rel

        frontmatter = dict(document.frontmatter)
        frontmatter["type"] = normalized_type
        if target_rel.parts[:1] == ("sources",) or normalized_type == "source":
            frontmatter["canonical"] = False
            frontmatter["status"] = "done"
            frontmatter["source_kind"] = self._source_kind_for_classification(classified.doc_class)
            frontmatter.pop("superseded_by", None)
        else:
            frontmatter.setdefault("canonical", False)
            frontmatter.setdefault("source_kind", self._source_kind_for_classification(classified.doc_class))
        frontmatter["confidence"] = classified.confidence
        body = document.body
        rendered = dump_markdown_document(frontmatter, body)
        atomic_write_text(target, rendered, encoding="utf-8")
        return target_rel

    def _evidence_frontmatter(
        self,
        *,
        target_rel: Path,
        classified: ClassifiedDocument,
        normalized_type: str,
    ) -> dict[str, object]:
        frontmatter: dict[str, object] = {
            "title": target_rel.stem.replace("-", " ").title(),
            "type": normalized_type,
            "confidence": classified.confidence,
        }
        if target_rel.parts[:1] == ("sources",) or normalized_type == "source":
            frontmatter["canonical"] = False
            frontmatter["status"] = "done"
            frontmatter["source_kind"] = self._source_kind_for_classification(classified.doc_class)
            return frontmatter
        frontmatter["canonical"] = False
        frontmatter["source_kind"] = self._source_kind_for_classification(classified.doc_class)
        return frontmatter

    def _write_canonical_subject(
        self,
        subject_ref: str,
        atoms: tuple[MemoryAtom, ...],
        contradictions: list[dict[str, str]],
        subject_aliases: dict[str, set[str]],
    ) -> Path | None:
        if subject_ref.startswith("core:"):
            return None
        try:
            target_rel = Path(canonical_target_for_subject(subject_ref))
        except ValueError:
            return None

        family = self._family_from_subject(subject_ref)
        slug = target_rel.parent.name if target_rel.name == "state.md" else target_rel.stem
        title = self._title_from_atoms(atoms, fallback=canonical_title_from_subject(subject_ref), subject_ref=subject_ref)

        winner = atoms[0]
        for candidate in atoms[1:]:
            chosen = choose_winning_atom(winner, candidate)
            if self._atoms_conflict(winner, candidate):
                contradictions.append(
                    build_contradiction_record(
                        subject_ref=subject_ref,
                        left=winner,
                        right=candidate,
                        reason="precedence-based canonical synthesis",
                    ).to_dict()
                )
            winner = chosen

        aliases = tuple(sorted(subject_aliases.get(subject_ref, set())))
        claim_history = tuple(self._claim_record_from_atom(subject_ref, atom) for atom in atoms)
        active_claims = tuple(
            claim for claim in claim_history if claim.claim_id == self._claim_record_from_atom(subject_ref, winner).claim_id
        )
        update = render_canonical_from_claims(
            family=family,
            title=title,
            entity_id=subject_ref,
            claims=active_claims,
            history=claim_history,
            aliases=aliases,
        )

        target = self.output_root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, update.body, encoding="utf-8")
        return target_rel

    def _write_quarantine(self, rel_path: Path, text: str, reason: str) -> Path:
        target_rel = Path("inbox/quarantine") / self._markdown_target_name(rel_path.name)
        target = self.output_root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        wrapped = dump_markdown_document(
            {
                "title": f"Quarantine {rel_path.stem}",
                "type": "note",
                "status": "raw",
                "canonical": False,
                "source_kind": "imported",
                "confidence": "low",
            },
            f"Reason: {reason}\n\n{text}",
        )
        atomic_write_text(target, wrapped, encoding="utf-8")
        return target_rel

    def _is_supported_stage_path(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_STAGE_SUFFIXES

    def _load_legacy_text(self, path: Path, *, rel_path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix == ".json":
            return self._render_json_as_markdown(rel_path=rel_path, raw_text=text)
        if suffix in {".jsonl", ".ndjson"}:
            return self._render_json_lines_as_markdown(rel_path=rel_path, raw_text=text)
        if suffix == ".txt":
            return self._render_text_as_markdown(rel_path=rel_path, raw_text=text)
        if suffix in {".yaml", ".yml"}:
            return self._render_yaml_as_markdown(rel_path=rel_path, raw_text=text)
        if suffix == ".toml":
            return self._render_toml_as_markdown(rel_path=rel_path, raw_text=text)
        if suffix == ".csv":
            return self._render_csv_as_markdown(rel_path=rel_path, raw_text=text)
        return text

    def _render_json_as_markdown(self, *, rel_path: Path, raw_text: str) -> str:
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            pretty_json = raw_text.strip()
            title = rel_path.stem.replace("-", " ").replace("_", " ").strip().title() or rel_path.name
            return "\n".join(
                (
                    f"# {title}",
                    "",
                    f"Source file: {rel_path.as_posix()}",
                    "Source format: json",
                    "",
                    "## Raw JSON",
                    "",
                    "```json",
                    pretty_json,
                    "```",
                    "",
                )
            )

        title = self._json_title(payload, fallback=rel_path.stem.replace("-", " ").replace("_", " ").strip().title())
        field_lines = self._json_summary_lines(payload)
        pretty_json = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)
        lines = [
            f"# {title}",
            "",
            f"Source file: {rel_path.as_posix()}",
            "Source format: json",
        ]
        if field_lines:
            lines.extend(("", "## Extracted Fields", "", *field_lines))
        lines.extend(("", "## Raw JSON", "", "```json", pretty_json, "```", ""))
        return "\n".join(lines)

    def _json_title(self, payload: object, *, fallback: str) -> str:
        if isinstance(payload, dict):
            for key in ("title", "name", "subject"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if fallback.strip():
            return fallback.strip()
        return "Imported JSON"

    def _json_summary_lines(self, payload: object) -> tuple[str, ...]:
        if isinstance(payload, dict):
            lines: list[str] = []
            for key, value in payload.items():
                if isinstance(value, str):
                    lines.append(f"- {key}: {value}")
                    continue
                if isinstance(value, (int, float, bool)):
                    lines.append(f"- {key}: {json.dumps(value)}")
                    continue
                if isinstance(value, list) and value and all(isinstance(item, (str, int, float, bool)) for item in value):
                    rendered_items = ", ".join(json.dumps(item) if isinstance(item, bool) else str(item) for item in value)
                    lines.append(f"- {key}: {rendered_items}")
                    continue
                if isinstance(value, list):
                    lines.append(f"- {key}: list[{len(value)}]")
                    continue
                if isinstance(value, dict):
                    lines.append(f"- {key}: object[{len(value)}]")
            return tuple(lines)
        if isinstance(payload, list):
            return (f"- items: list[{len(payload)}]",)
        return ()

    def _render_json_lines_as_markdown(self, *, rel_path: Path, raw_text: str) -> str:
        format_label = rel_path.suffix.lower().lstrip(".") or "jsonl"
        records: list[dict[str, object]] = []
        raw_lines = [line.rstrip() for line in raw_text.splitlines() if line.strip()]
        for line in raw_lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)

        if not records:
            title = rel_path.stem.replace("-", " ").replace("_", " ").strip().title() or rel_path.name
            return "\n".join(
                (
                    f"# {title}",
                    "",
                    f"Source file: {rel_path.as_posix()}",
                    f"Source format: {format_label}",
                    "",
                    f"## Raw {format_label.upper()}",
                    "",
                    "```json",
                    raw_text.strip(),
                    "```",
                    "",
                )
            )

        title = self._json_lines_title(records, fallback=rel_path.stem)
        transcript_lines = self._json_lines_transcript(records)
        summary_lines = self._json_lines_summary(records)
        lines = [
            f"# {title}",
            "",
            f"Source file: {rel_path.as_posix()}",
            f"Source format: {format_label}",
        ]
        if summary_lines:
            lines.extend(("", "## Extracted Records", "", *summary_lines))
        if transcript_lines:
            lines.extend(("", "## Extracted Transcript", "", *transcript_lines))
        lines.extend(("", f"## Raw {format_label.upper()}", "", "```json", raw_text.strip(), "```", ""))
        return "\n".join(lines)

    def _render_text_as_markdown(self, *, rel_path: Path, raw_text: str) -> str:
        title = self._default_import_title(rel_path)
        return "\n".join(
            (
                f"# {title}",
                "",
                f"Source file: {rel_path.as_posix()}",
                "Source format: txt",
                "",
                "## Raw Text",
                "",
                "```text",
                raw_text.strip(),
                "```",
                "",
            )
        )

    def _render_yaml_as_markdown(self, *, rel_path: Path, raw_text: str) -> str:
        format_label = rel_path.suffix.lower().lstrip(".") or "yaml"
        try:
            payload = yaml.safe_load(raw_text)
        except yaml.YAMLError:
            payload = None
        return self._render_structured_text_as_markdown(
            rel_path=rel_path,
            raw_text=raw_text,
            format_label=format_label,
            payload=payload,
        )

    def _render_toml_as_markdown(self, *, rel_path: Path, raw_text: str) -> str:
        try:
            payload = tomllib.loads(raw_text)
        except tomllib.TOMLDecodeError:
            payload = None
        return self._render_structured_text_as_markdown(
            rel_path=rel_path,
            raw_text=raw_text,
            format_label="toml",
            payload=payload,
        )

    def _render_structured_text_as_markdown(
        self,
        *,
        rel_path: Path,
        raw_text: str,
        format_label: str,
        payload: object,
    ) -> str:
        title = self._json_title(payload, fallback=self._default_import_title(rel_path))
        field_lines = self._json_summary_lines(payload)
        lines = [
            f"# {title}",
            "",
            f"Source file: {rel_path.as_posix()}",
            f"Source format: {format_label}",
        ]
        if field_lines:
            lines.extend(("", "## Extracted Fields", "", *field_lines))
        lines.extend(
            (
                "",
                f"## Raw {format_label.upper()}",
                "",
                f"```{format_label}",
                raw_text.strip(),
                "```",
                "",
            )
        )
        return "\n".join(lines)

    def _render_csv_as_markdown(self, *, rel_path: Path, raw_text: str) -> str:
        rows = list(csv.reader(raw_text.splitlines()))
        title = self._default_import_title(rel_path)
        lines = [
            f"# {title}",
            "",
            f"Source file: {rel_path.as_posix()}",
            "Source format: csv",
        ]
        if rows:
            header = rows[0]
            data_rows = rows[1:] if len(rows) > 1 else []
            if header:
                lines.extend(("", "## Extracted Rows", "", f"- columns: {', '.join(header)}", f"- row_count: {len(data_rows)}"))
        lines.extend(("", "## Raw CSV", "", "```csv", raw_text.strip(), "```", ""))
        return "\n".join(lines)

    def _default_import_title(self, rel_path: Path) -> str:
        title = rel_path.stem.replace("-", " ").replace("_", " ").strip().title()
        return title or rel_path.name

    def _json_lines_title(self, records: list[dict[str, object]], *, fallback: str) -> str:
        for record in records:
            for key in ("session_id", "sessionId", "title", "name"):
                value = record.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        cleaned = fallback.replace("-", " ").replace("_", " ").strip()
        return cleaned or "Imported Transcript"

    def _session_target_for_structured_transcript(self, rel_path: Path, text: str) -> str | None:
        if rel_path.suffix.lower() not in {".jsonl", ".ndjson"}:
            return None
        if "## Extracted Transcript" not in text:
            return None
        session_name = normalize_migration_slug(self._structured_title(text) or rel_path.stem) or "session"
        date_prefix = self._structured_date(text)
        file_name = f"{date_prefix}-{session_name}.md" if date_prefix else f"{session_name}.md"
        return f"logs/sessions/imported/{file_name}"

    def _structured_title(self, text: str) -> str | None:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                value = stripped.removeprefix("# ").strip()
                if value:
                    return value
        return None

    def _structured_date(self, text: str) -> str | None:
        try:
            document = load_markdown_document(text)
        except ValueError:
            document = None
        if document is not None:
            for key in _STRUCTURED_DATE_KEYS:
                time_ref = self._normalized_date_value(document.frontmatter.get(key))
                if time_ref is not None:
                    return time_ref
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            for key in _STRUCTURED_DATE_KEYS:
                prefix = f"- {key}: "
                if stripped.startswith(prefix):
                    time_ref = self._normalized_date_value(stripped.removeprefix(prefix).strip())
                    if time_ref is not None:
                        return time_ref
        return None

    def _normalized_date_value(self, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[:10]
        return None

    def _transcript_lines(self, text: str) -> tuple[tuple[str | None, str], ...]:
        lines = text.splitlines()
        transcript: list[tuple[str | None, str]] = []
        in_transcript = False
        for line in lines:
            stripped = line.strip()
            if stripped == "## Extracted Transcript":
                in_transcript = True
                continue
            if not in_transcript:
                continue
            if stripped.startswith("## "):
                break
            if not stripped.startswith("- "):
                continue
            entry = stripped.removeprefix("- ").strip()
            speaker: str | None = None
            utterance = entry
            if ": " in entry:
                candidate_speaker, candidate_text = entry.split(": ", 1)
                if candidate_speaker in {"user", "assistant"}:
                    speaker = candidate_speaker
                    utterance = candidate_text.strip()
            if utterance:
                transcript.append((speaker, utterance))
        return tuple(transcript)

    def _json_lines_summary(self, records: list[dict[str, object]]) -> tuple[str, ...]:
        roles = tuple(
            self._dedupe_preserve_order(
                role
                for record in records
                if isinstance((role := self._record_role(record)), str) and role
            )
        )
        types = tuple(
            self._dedupe_preserve_order(
                record_type
                for record in records
                if isinstance((record_type := record.get("type")), str) and record_type.strip()
            )
        )
        timestamps = tuple(
            value.strip()
            for record in records
            if isinstance((value := record.get("timestamp")), str) and value.strip()
        )
        summary = [f"- record_count: {len(records)}"]
        if roles:
            summary.append(f"- roles: {', '.join(roles)}")
        if types:
            summary.append(f"- event_types: {', '.join(types)}")
        if timestamps:
            summary.append(f"- first_timestamp: {timestamps[0]}")
            summary.append(f"- last_timestamp: {timestamps[-1]}")
        return tuple(summary)

    def _json_lines_transcript(self, records: list[dict[str, object]]) -> tuple[str, ...]:
        transcript: list[str] = []
        for record in records:
            speaker = self._record_role(record)
            text = self._record_text(record)
            if not text:
                continue
            prefix = f"{speaker}: " if speaker else ""
            transcript.append(f"- {prefix}{text}")
        return tuple(transcript)

    def _record_role(self, record: dict[str, object]) -> str | None:
        for value in (record.get("role"),):
            if isinstance(value, str) and value.strip():
                return value.strip()
        message = record.get("message")
        if isinstance(message, dict):
            value = message.get("role")
            if isinstance(value, str) and value.strip():
                return value.strip()
        event_type = record.get("type")
        if isinstance(event_type, str):
            normalized = event_type.strip().lower()
            if normalized in {"user", "user_message"}:
                return "user"
            if normalized in {"assistant", "assistant_message"}:
                return "assistant"
        return None

    def _record_text(self, record: dict[str, object]) -> str:
        for value in (
            self._content_text(record.get("content")),
            self._message_text(record.get("message")),
        ):
            if value:
                return value
        return ""

    def _message_text(self, message: object) -> str:
        if isinstance(message, str) and message.strip():
            return message.strip()
        if isinstance(message, dict):
            for key in ("text", "message"):
                value = message.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            content_text = self._content_text(message.get("content"))
            if content_text:
                return content_text
        return ""

    def _content_text(self, content: object) -> str:
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                    continue
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if isinstance(item_type, str) and item_type.strip().lower() == "reasoning":
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            return " ".join(parts).strip()
        return ""

    def _markdown_target_name(self, name: str) -> str:
        return name if name.lower().endswith(".md") else f"{name}.md"

    def _markdown_rel_path(self, rel_path: Path) -> Path:
        if rel_path.suffix.lower() == ".md":
            return rel_path
        return rel_path.with_name(self._markdown_target_name(rel_path.name))

    def _write_report(
        self,
        *,
        run_id: str,
        staged_count: int,
        written_count: int,
        canonical_created_count: int,
        quarantined_count: int,
        contradictions: list[dict[str, str]],
        audits: tuple[MigrationPageAudit, ...],
        repairs: tuple[MigrationPageRepair, ...],
        audit_artifact_path: Path | None,
        repair_artifact_path: Path | None,
        fallback_warnings: tuple[MigrationFallbackWarning, ...],
    ) -> Path:
        report_rel = Path("references/reports/migrations") / f"{run_id}.md"
        report = [
            "# Migration Report",
            "",
            f"- staged_count: {staged_count}",
            f"- written_count: {written_count}",
            f"- canonical_created_count: {canonical_created_count}",
            f"- quarantined_count: {quarantined_count}",
        ]
        if contradictions:
            report.extend(["", "## Contradictions"])
            report.extend(
                f"- `{item['subject_ref']}` winner `{item['winner_path']}` over `{item['left_path']}` / `{item['right_path']}`"
                for item in contradictions
            )
        if audit_artifact_path is not None:
            report.extend(["", "## Audit Artifact", f"- {audit_artifact_path.as_posix()}"])
        if repair_artifact_path is not None:
            report.extend(["", "## Repair Artifact", f"- {repair_artifact_path.as_posix()}"])
        if fallback_warnings:
            report.extend(["", "## Fallback Warnings"])
            for warning in fallback_warnings:
                scope = f" `{warning.scope}`" if warning.scope is not None else ""
                report.append(f"- `{warning.stage}`{scope} {warning.message}")
        applied_repairs = [repair for repair in repairs if repair.apply]
        if applied_repairs:
            report.extend(["", "## Repairs Applied"])
            for repair in applied_repairs:
                report.append(f"- `{repair.path}` {repair.summary}")
        flagged_audits = [audit for audit in audits if audit.verdict != "pass"]
        if flagged_audits:
            report.extend(["", "## QA Findings"])
            for audit in flagged_audits:
                report.append(f"- `{audit.path}` [{audit.verdict}] {audit.summary}")
        target = self.output_root / report_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, "\n".join(report).rstrip() + "\n", encoding="utf-8")
        return report_rel

    def _write_run_artifact(
        self,
        *,
        run_id: str,
        legacy_root: Path,
        staged_count: int,
        written_count: int,
        canonical_created_count: int,
        quarantined_count: int,
        contradictions: list[dict[str, str]],
        aliases: dict[str, str],
        audits: tuple[MigrationPageAudit, ...],
        repairs: tuple[MigrationPageRepair, ...],
        audit_artifact_path: Path | None,
        repair_artifact_path: Path | None,
        fallback_warnings: tuple[MigrationFallbackWarning, ...],
    ) -> Path:
        target_rel = Path("inbox/migration-runs") / f"{run_id}.json"
        target = self.output_root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            target,
            json.dumps(
                {
                    "legacy_root": legacy_root.as_posix(),
                    "staged_count": staged_count,
                    "written_count": written_count,
                    "canonical_created_count": canonical_created_count,
                    "quarantined_count": quarantined_count,
                    "contradictions": contradictions,
                    "aliases": aliases,
                    "audit_artifact_path": None if audit_artifact_path is None else audit_artifact_path.as_posix(),
                    "repair_artifact_path": None if repair_artifact_path is None else repair_artifact_path.as_posix(),
                    "fallback_warnings": [warning.to_dict() for warning in fallback_warnings],
                    "audits": [audit.to_dict() for audit in audits],
                    "repairs": [repair.to_dict() for repair in repairs],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return target_rel

    def _write_audit_artifact(self, *, run_id: str, audits: tuple[MigrationPageAudit, ...]) -> Path:
        target_rel = Path("inbox/migration-runs") / f"{run_id}.audit.json"
        target = self.output_root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            target,
            json.dumps({"audits": [audit.to_dict() for audit in audits]}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target_rel

    def _write_repair_artifact(self, *, run_id: str, repairs: tuple[MigrationPageRepair, ...]) -> Path:
        target_rel = Path("inbox/migration-runs") / f"{run_id}.repair.json"
        target = self.output_root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            target,
            json.dumps({"repairs": [repair.to_dict() for repair in repairs]}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target_rel

    def _audit_generated_pages(
        self,
        written_paths: tuple[Path, ...],
        *,
        fallback_warnings: list[MigrationFallbackWarning],
    ) -> tuple[MigrationPageAudit, ...]:
        if self.llm is None or not written_paths:
            return ()
        pages: list[dict[str, object]] = []
        for rel_path in written_paths:
            target = self.output_root / rel_path
            if not target.exists():
                continue
            pages.append(
                {
                    "path": rel_path.as_posix(),
                    "content": target.read_text(encoding="utf-8"),
                }
            )
        if not pages:
            return ()
        try:
            return self.llm.audit_migration_pages(pages=tuple(pages))
        except Exception as err:
            fallback_warnings.append(
                MigrationFallbackWarning(
                    stage="audit",
                    message=self._format_exception(err),
                )
            )
            return ()

    def _repair_generated_pages(
        self,
        written_paths: tuple[Path, ...],
        *,
        audits: tuple[MigrationPageAudit, ...],
        entity_ids_by_path: dict[Path, str],
        claim_store: ClaimStore,
        fallback_warnings: list[MigrationFallbackWarning],
    ) -> tuple[MigrationPageRepair, ...]:
        if self.llm is None or not written_paths:
            return ()
        flagged = {audit.path: audit for audit in audits if audit.verdict != "pass"}
        if not flagged:
            return ()
        pages: list[dict[str, object]] = []
        for rel_path in written_paths:
            audit = flagged.get(rel_path.as_posix())
            if audit is None:
                continue
            target = self.output_root / rel_path
            if not target.exists():
                continue
            entity_id = entity_ids_by_path.get(rel_path)
            pages.append(
                {
                    "path": rel_path.as_posix(),
                    "verdict": audit.verdict,
                    "summary": audit.summary,
                    "issues": list(audit.issues),
                    "content": target.read_text(encoding="utf-8"),
                    "entity_id": entity_id,
                    "current_claims": self._serialize_claim_records(
                        claim_store.current_claims(entity_id) if entity_id is not None else ()
                    ),
                    "claim_history": self._serialize_claim_records(
                        claim_store.claim_history(entity_id) if entity_id is not None else ()
                    ),
                    "claim_events": self._serialize_claim_events(
                        claim_store.claim_events(entity_id) if entity_id is not None else ()
                    ),
                }
            )
        if not pages:
            return ()
        try:
            return self.llm.repair_migration_pages(pages=tuple(pages))
        except Exception as err:
            fallback_warnings.append(
                MigrationFallbackWarning(
                    stage="repair",
                    message=self._format_exception(err),
                )
            )
            return ()

    def _apply_page_repairs(self, repairs: tuple[MigrationPageRepair, ...]) -> None:
        for repair in repairs:
            if not repair.apply or not repair.content.strip():
                continue
            target = self.output_root / Path(repair.path)
            if not target.exists():
                continue
            try:
                load_markdown_document(repair.content)
            except ValueError:
                continue
            atomic_write_text(target, repair.content.rstrip() + "\n", encoding="utf-8")

    def _write_document_artifact(self, *, run_id: str, prepared: PreparedMigrationDocument) -> Path:
        target_rel = Path("inbox/migration-documents") / run_id / prepared.rel_path.with_suffix(".json")
        target = self.output_root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            target,
            json.dumps(
                {
                    "path": prepared.rel_path.as_posix(),
                    "classified": prepared.classified.to_dict(),
                    "used_llm_for_classification": prepared.used_llm_for_classification,
                    "used_llm_for_extraction": prepared.used_llm_for_extraction,
                    "resolution_mode": prepared.resolution_mode,
                    "quarantine_reason": prepared.quarantine_reason,
                    "fallback_reasons": list(prepared.fallback_reasons),
                    "entity_candidates": [candidate.to_dict() for candidate in prepared.entity_candidates],
                    "atoms": [atom.to_dict() for atom in prepared.extracted_atoms],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return target_rel

    def _resolve_documents(
        self,
        documents: list[ResolvedMigrationDocument],
        *,
        registry: EntityRegistry,
        claim_store: ClaimStore,
        alias_map: dict[str, str],
        subject_aliases: dict[str, set[str]],
        fallback_warnings: list[MigrationFallbackWarning],
    ) -> list[ResolvedMigrationDocument]:
        clustered_entity_ids, clustered_aliases, clustered_titles = self._cluster_entity_candidates(
            documents,
            registry=registry,
            fallback_warnings=fallback_warnings,
        )
        resolved_documents: list[ResolvedMigrationDocument] = []
        for document in documents:
            subject_map, entity_ids = self._resolve_document_entities(
                document,
                registry=registry,
                alias_map=alias_map,
                subject_aliases=subject_aliases,
                clustered_entity_ids=clustered_entity_ids,
                clustered_aliases=clustered_aliases,
                clustered_titles=clustered_titles,
            )
            resolved_atoms = tuple(self._resolve_document_atom(atom, subject_map) for atom in document.atoms)
            self._record_claims(resolved_atoms, claim_store=claim_store)
            resolved_documents.append(
                ResolvedMigrationDocument(
                    rel_path=document.rel_path,
                    classified=document.classified,
                    evidence_target=document.evidence_target,
                    atoms=resolved_atoms,
                    entity_candidates=document.entity_candidates,
                    entity_ids=entity_ids,
                )
            )
        return resolved_documents

    def _resolve_document_entities(
        self,
        document: ResolvedMigrationDocument,
        *,
        registry: EntityRegistry,
        alias_map: dict[str, str],
        subject_aliases: dict[str, set[str]],
        clustered_entity_ids: dict[str, str],
        clustered_aliases: dict[str, set[str]],
        clustered_titles: dict[str, str],
    ) -> tuple[dict[str, str], tuple[str, ...]]:
        subject_map: dict[str, str] = {}
        resolved_entity_ids: list[str] = []
        classification_subject = self._canonical_subject_from_classification(document.classified)

        for candidate_index, candidate in enumerate(document.entity_candidates):
            mention_key = self._entity_mention_key(document.rel_path, candidate_index, candidate)
            resolved_id = clustered_entity_ids.get(mention_key)
            if resolved_id is None:
                resolved_id = self._resolve_entity_candidate(
                    candidate,
                    registry=registry,
                    classification_subject=classification_subject,
                )
            normalized_ref = self._normalize_subject_ref(candidate.ref)
            subject_map[normalized_ref] = resolved_id
            alias_map[normalized_ref] = resolved_id
            if resolved_id not in resolved_entity_ids:
                resolved_entity_ids.append(resolved_id)
            resolved_title = clustered_titles.get(resolved_id) or candidate.display_name.strip() or canonical_title_from_subject(resolved_id)
            aliases = set(infer_aliases_from_subject(resolved_id, requested_subject=resolved_title))
            aliases.update(alias.strip() for alias in candidate.aliases if alias.strip())
            aliases.update(clustered_aliases.get(resolved_id, set()))
            subject_aliases.setdefault(resolved_id, set()).update(aliases)
            registry.upsert(
                entity_id=resolved_id,
                family=self._family_from_subject(resolved_id),
                title=resolved_title,
                target_path=canonical_target_for_subject(resolved_id),
                aliases=tuple(sorted(subject_aliases.get(resolved_id, set()))),
            )

        if classification_subject is not None and classification_subject not in resolved_entity_ids:
            same_family_resolved = next(
                (
                    entity_id
                    for entity_id in resolved_entity_ids
                    if self._family_from_subject(entity_id) == self._family_from_subject(classification_subject)
                ),
                None,
            )
            if same_family_resolved is not None:
                alias_map[classification_subject] = same_family_resolved
                subject_aliases.setdefault(same_family_resolved, set()).update(infer_aliases_from_subject(classification_subject))
                registry.upsert(
                    entity_id=same_family_resolved,
                    family=self._family_from_subject(same_family_resolved),
                    title=clustered_titles.get(same_family_resolved) or canonical_title_from_subject(same_family_resolved),
                    target_path=canonical_target_for_subject(same_family_resolved),
                    aliases=tuple(sorted(subject_aliases.get(same_family_resolved, set()))),
                )
                return subject_map, tuple(resolved_entity_ids)
            resolved_entity_ids.append(classification_subject)
            alias_map[classification_subject] = classification_subject
            subject_aliases.setdefault(classification_subject, set()).update(infer_aliases_from_subject(classification_subject))
            registry.upsert(
                entity_id=classification_subject,
                family=self._family_from_subject(classification_subject),
                title=clustered_titles.get(classification_subject) or canonical_title_from_subject(classification_subject),
                target_path=canonical_target_for_subject(classification_subject),
                aliases=tuple(sorted(subject_aliases.get(classification_subject, set()))),
            )
        for atom in document.atoms:
            normalized_ref = self._normalize_subject_ref(atom.subject_ref)
            resolved_ref = subject_map.get(normalized_ref, normalized_ref)
            if (
                ":" not in normalized_ref
                or normalized_ref.startswith("core:")
                or normalized_ref in subject_map
                or resolved_ref in resolved_entity_ids
            ):
                continue
            title = self._title_from_atoms((atom,), fallback=canonical_title_from_subject(normalized_ref), subject_ref=normalized_ref)
            alias_map[normalized_ref] = normalized_ref
            subject_aliases.setdefault(normalized_ref, set()).update(infer_aliases_from_subject(normalized_ref, requested_subject=title))
            registry.upsert(
                entity_id=normalized_ref,
                family=self._family_from_subject(normalized_ref),
                title=title,
                target_path=canonical_target_for_subject(normalized_ref),
                aliases=tuple(sorted(subject_aliases.get(normalized_ref, set()))),
            )
            resolved_entity_ids.append(normalized_ref)
        return subject_map, tuple(resolved_entity_ids)

    def _cluster_entity_candidates(
        self,
        documents: list[ResolvedMigrationDocument],
        *,
        registry: EntityRegistry,
        fallback_warnings: list[MigrationFallbackWarning],
    ) -> tuple[dict[str, str], dict[str, set[str]], dict[str, str]]:
        if self.llm is None:
            return {}, {}, {}
        mentions_by_family: dict[str, list[MigrationEntityMention]] = {}
        for document in documents:
            classification_subject = self._canonical_subject_from_classification(document.classified)
            for candidate_index, candidate in enumerate(document.entity_candidates):
                family = self._family_for_candidate(candidate, classification_subject=classification_subject)
                if family is None:
                    continue
                normalized_ref = self._normalized_candidate_ref(candidate, classification_subject=classification_subject)
                mentions_by_family.setdefault(family, []).append(
                    MigrationEntityMention(
                        key=self._entity_mention_key(document.rel_path, candidate_index, candidate),
                        ref=normalized_ref,
                        family=family,
                        display_name=candidate.display_name.strip() or canonical_title_from_subject(normalized_ref),
                        aliases=tuple(alias.strip() for alias in candidate.aliases if alias.strip()),
                        source_path=document.rel_path.as_posix(),
                    )
                )

        clustered_entity_ids: dict[str, str] = {}
        clustered_aliases: dict[str, set[str]] = {}
        clustered_titles: dict[str, str] = {}
        for family, mentions in mentions_by_family.items():
            if len(mentions) < 2:
                continue
            existing_entities = tuple(
                {
                    "entity_id": record.entity_id,
                    "family": record.family,
                    "title": record.title,
                    "target_path": record.target_path,
                    "aliases": list(record.aliases),
                }
                for record in registry.list_family(family)
            )
            try:
                clusters = self.llm.resolve_entity_mentions(
                    family=family,
                    candidates=tuple(mentions),
                    existing_entities=existing_entities,
                )
            except Exception as err:
                fallback_warnings.append(
                    MigrationFallbackWarning(
                        stage="entity_resolution",
                        scope=family,
                        message=self._format_exception(err),
                    )
                )
                continue
            mention_lookup = {mention.key: mention for mention in mentions}
            for cluster in clusters:
                canonical_ref = self._normalize_cluster_ref(
                    cluster.canonical_ref,
                    family=family,
                    display_name=cluster.display_name,
                )
                clustered_titles[canonical_ref] = cluster.display_name.strip() or canonical_title_from_subject(canonical_ref)
                aliases = clustered_aliases.setdefault(canonical_ref, set())
                aliases.add(clustered_titles[canonical_ref])
                aliases.update(alias.strip() for alias in cluster.aliases if alias.strip())
                for member_key in cluster.member_keys:
                    mention = mention_lookup.get(member_key)
                    if mention is None:
                        continue
                    clustered_entity_ids[member_key] = canonical_ref
                    aliases.add(mention.display_name)
                    aliases.update(alias.strip() for alias in mention.aliases if alias.strip())
        return clustered_entity_ids, clustered_aliases, clustered_titles

    def _family_for_candidate(
        self,
        candidate: MigrationEntityCandidate,
        *,
        classification_subject: str | None,
    ) -> str | None:
        normalized_ref = self._normalize_subject_ref(candidate.ref)
        if ":" in normalized_ref:
            return self._family_from_subject(normalized_ref)
        if classification_subject is None:
            return None
        return self._family_from_subject(classification_subject)

    def _normalized_candidate_ref(
        self,
        candidate: MigrationEntityCandidate,
        *,
        classification_subject: str | None,
    ) -> str:
        normalized_ref = self._normalize_subject_ref(candidate.ref)
        if ":" in normalized_ref:
            return normalized_ref
        if classification_subject is None:
            raise ValueError(f"entity candidate ref missing family: {candidate.ref}")
        family = self._family_from_subject(classification_subject)
        return f"{family}:{normalize_migration_slug(normalized_ref)}"

    def _normalize_cluster_ref(self, candidate_ref: str, *, family: str, display_name: str) -> str:
        normalized_ref = self._normalize_subject_ref(candidate_ref)
        if ":" in normalized_ref and self._family_from_subject(normalized_ref) == family:
            return normalized_ref
        fallback_slug = normalize_migration_slug(display_name) or normalize_migration_slug(normalized_ref)
        return f"{family}:{fallback_slug}"

    def _entity_mention_key(
        self,
        rel_path: Path,
        candidate_index: int,
        candidate: MigrationEntityCandidate,
    ) -> str:
        return f"{rel_path.as_posix()}::{candidate_index}::{self._normalize_subject_ref(candidate.ref)}"

    def _resolve_entity_candidate(
        self,
        candidate: MigrationEntityCandidate,
        *,
        registry: EntityRegistry,
        classification_subject: str | None,
    ) -> str:
        normalized_ref = self._normalize_subject_ref(candidate.ref)
        if ":" not in normalized_ref:
            if classification_subject is None:
                raise ValueError(f"entity candidate ref missing family: {candidate.ref}")
            family = self._family_from_subject(classification_subject)
            normalized_ref = f"{family}:{normalize_migration_slug(normalized_ref)}"
        family = self._family_from_subject(normalized_ref)
        existing = registry.get(normalized_ref)
        if existing is not None:
            return existing.entity_id
        for value in (candidate.display_name, *candidate.aliases):
            match = registry.resolve(value, family=family)
            if match is not None:
                return match.entity_id
        if classification_subject is not None and self._family_from_subject(classification_subject) == family:
            return classification_subject
        return normalized_ref

    def _resolve_document_atom(self, atom: MemoryAtom, subject_map: dict[str, str]) -> MemoryAtom:
        normalized_ref = self._normalize_subject_ref(atom.subject_ref)
        resolved_ref = subject_map.get(normalized_ref, normalized_ref)
        if resolved_ref == atom.subject_ref:
            return atom
        return MemoryAtom(
            kind=atom.kind,
            subject_ref=resolved_ref,
            payload=dict(atom.payload),
            evidence_path=atom.evidence_path,
            time_ref=atom.time_ref,
            confidence=atom.confidence,
        )

    def _write_canonical_subject_from_store(
        self,
        entity_id: str,
        *,
        registry: EntityRegistry,
        claim_store: ClaimStore,
        subject_aliases: dict[str, set[str]],
    ) -> Path | None:
        record = registry.get(entity_id)
        history = claim_store.claim_history(entity_id)
        if not history:
            return None
        current_claims = claim_store.current_claims(entity_id)
        events = claim_store.claim_events(entity_id)
        family = record.family if record is not None else self._family_from_subject(entity_id)
        title = record.title if record is not None else canonical_title_from_subject(entity_id)
        target_rel = Path(record.target_path if record is not None else canonical_target_for_subject(entity_id))
        aliases = set(subject_aliases.get(entity_id, set()))
        if record is not None:
            aliases.update(record.aliases)
        update = render_canonical_from_claims(
            family=family,
            title=title,
            entity_id=entity_id,
            claims=current_claims,
            history=history,
            events=events,
            aliases=tuple(sorted(aliases)),
        )
        target = self.output_root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, update.body, encoding="utf-8")
        return target_rel

    def _collect_contradictions(self, grouped_atoms: dict[str, tuple[MemoryAtom, ...]]) -> list[dict[str, str]]:
        contradictions: list[dict[str, str]] = []
        for subject_ref, subject_atoms in grouped_atoms.items():
            if not subject_atoms:
                continue
            winner = subject_atoms[0]
            for candidate in subject_atoms[1:]:
                if self._atoms_conflict(winner, candidate):
                    contradictions.append(
                        build_contradiction_record(
                            subject_ref=subject_ref,
                            left=winner,
                            right=candidate,
                            reason="precedence-based canonical synthesis",
                        ).to_dict()
                    )
                winner = choose_winning_atom(winner, candidate)
        return contradictions

    def _group_atoms(self, atoms: Iterable[MemoryAtom]) -> dict[str, tuple[MemoryAtom, ...]]:
        grouped: dict[str, list[MemoryAtom]] = {}
        for atom in atoms:
            grouped.setdefault(self._normalize_subject_ref(atom.subject_ref), []).append(atom)
        return {key: tuple(values) for key, values in grouped.items()}

    def _record_claims(self, atoms: Iterable[MemoryAtom], *, claim_store: ClaimStore) -> None:
        for atom in atoms:
            statement = self._summary_from_atom(atom.subject_ref, atom).strip()
            if not statement:
                continue
            entity_id = self._normalize_subject_ref(atom.subject_ref)
            claim_kind, write_mode = self._claim_write_policy(atom)
            current_claims = claim_store.current_claims(entity_id, kind=claim_kind)
            if any(self._normalize_semantic_text(claim.statement) == self._normalize_semantic_text(statement) for claim in current_claims):
                continue
            if write_mode == "replace" and current_claims:
                claim_store.replace_current_claim(
                    entity_id=entity_id,
                    kind=claim_kind,
                    statement=statement,
                    evidence_path=atom.evidence_path,
                    confidence=atom.confidence,
                    reason="migration reconciliation",
                    occurred_at=atom.time_ref,
                )
                continue
            claim_store.add_claim(
                entity_id=entity_id,
                kind=claim_kind,
                statement=statement,
                evidence_path=atom.evidence_path,
                confidence=atom.confidence,
                occurred_at=atom.time_ref,
            )

    def _claim_write_policy(self, atom: MemoryAtom) -> tuple[str, str]:
        if atom.kind == "project_update":
            return "state", "replace"
        if atom.kind == "decision":
            return "decision", "append"
        if atom.kind == "person_fact":
            return "fact", "append"
        if atom.kind == "concept_claim":
            return "fact", "append"
        if atom.kind == "preference":
            return "preference", "append"
        if atom.kind in {"goal", "open_question", "followup", "timeline_event"}:
            return "note", "append"
        return atom.kind, "append"

    def _normalize_semantic_text(self, text: str) -> str:
        return " ".join(text.strip().lower().split())

    def _serialize_claim_records(self, claims: Iterable[ClaimRecord]) -> list[dict[str, object]]:
        return [
            {
                "claim_id": claim.claim_id,
                "entity_id": claim.entity_id,
                "kind": claim.kind,
                "statement": claim.statement,
                "status": claim.status,
                "valid_from": claim.valid_from,
                "valid_to": claim.valid_to,
                "confidence": claim.confidence,
                "evidence_path": claim.evidence_path,
                "created_at": claim.created_at,
                "updated_at": claim.updated_at,
            }
            for claim in claims
        ]

    def _serialize_claim_events(self, events: Iterable[object]) -> list[dict[str, object]]:
        serialized: list[dict[str, object]] = []
        for event in events:
            event_id = getattr(event, "event_id", None)
            claim_id = getattr(event, "claim_id", None)
            entity_id = getattr(event, "entity_id", None)
            event_type = getattr(event, "event_type", None)
            reason = getattr(event, "reason", None)
            evidence_path = getattr(event, "evidence_path", None)
            created_at = getattr(event, "created_at", None)
            statement = getattr(event, "statement", None)
            kind = getattr(event, "kind", None)
            claim_status = getattr(event, "claim_status", None)
            serialized.append(
                {
                    "event_id": event_id,
                    "claim_id": claim_id,
                    "entity_id": entity_id,
                    "event_type": event_type,
                    "reason": reason,
                    "evidence_path": evidence_path,
                    "created_at": created_at,
                    "statement": statement,
                    "kind": kind,
                    "claim_status": claim_status,
                }
            )
        return serialized

    def _claim_record_from_atom(self, subject_ref: str, atom: MemoryAtom) -> ClaimRecord:
        timestamp = atom.time_ref or datetime.now(tz=UTC).date().isoformat()
        claim_id = f"{normalize_migration_slug(subject_ref)}-{normalize_migration_slug(atom.evidence_path)}-{normalize_migration_slug(atom.kind)}"
        return ClaimRecord(
            claim_id=claim_id,
            entity_id=self._normalize_subject_ref(atom.subject_ref),
            kind=atom.kind,
            statement=self._summary_from_atom(subject_ref, atom).strip(),
            status="active",
            valid_from=timestamp,
            valid_to=None,
            confidence=atom.confidence,
            evidence_path=atom.evidence_path,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def _register_aliases(
        self,
        classified: ClassifiedDocument,
        alias_map: dict[str, str],
        subject_aliases: dict[str, set[str]],
        *,
        registry: EntityRegistry,
    ) -> None:
        canonical_subject = self._canonical_subject_from_classification(classified)
        if canonical_subject is None:
            return
        alias_map[canonical_subject] = canonical_subject
        aliases = set(infer_aliases_from_subject(canonical_subject))
        canonical_family = canonical_subject.split(":", 1)[0]
        for ref in classified.entity_refs:
            normalized = self._normalize_subject_ref(ref)
            if normalized.split(":", 1)[0] != canonical_family:
                continue
            alias_map[normalized] = canonical_subject
            if normalized != canonical_subject:
                aliases.update(infer_aliases_from_subject(normalized))
        subject_aliases.setdefault(canonical_subject, set()).update(aliases)
        registry.upsert(
            entity_id=canonical_subject,
            family=canonical_family,
            title=canonical_title_from_subject(canonical_subject),
            target_path=canonical_target_for_subject(canonical_subject),
            aliases=tuple(sorted(aliases)),
        )

    def _canonical_subject_from_classification(self, classified: ClassifiedDocument) -> str | None:
        if classified.doc_class == "core_user":
            for ref in classified.entity_refs:
                normalized = self._normalize_subject_ref(ref)
                if normalized.startswith("person:"):
                    return normalized
            return "person:user"
        target = Path(classified.target_path)
        parts = target.parts
        if parts[:1] == ("people",) and target.suffix == ".md":
            return f"person:{normalize_migration_slug(target.stem)}"
        if parts[:1] == ("projects",) and target.name == "state.md":
            return f"project:{normalize_migration_slug(target.parent.name)}"
        if parts[:1] == ("concepts",) and target.suffix == ".md":
            return f"concept:{normalize_migration_slug(target.stem)}"
        if parts[:1] == ("decisions",) and target.suffix == ".md":
            return f"decision:{normalize_migration_slug(target.stem)}"
        return None

    def _resolve_atom_alias(self, atom: MemoryAtom, alias_map: dict[str, str]) -> MemoryAtom:
        normalized = self._normalize_subject_ref(atom.subject_ref)
        resolved = alias_map.get(normalized, normalized)
        if resolved == atom.subject_ref:
            return atom
        return MemoryAtom(
            kind=atom.kind,
            subject_ref=resolved,
            payload=dict(atom.payload),
            evidence_path=atom.evidence_path,
            time_ref=atom.time_ref,
            confidence=atom.confidence,
        )

    def _normalize_subject_ref(self, subject_ref: str) -> str:
        if ":" not in subject_ref:
            return subject_ref.strip().lower()
        family, raw_slug = subject_ref.split(":", 1)
        family = {
            "people": "person",
            "projects": "project",
            "concepts": "concept",
            "decisions": "decision",
        }.get(family.strip().lower(), family.strip().lower())
        slug = normalize_migration_slug(raw_slug)
        return f"{family}:{slug}"

    def _run_id(self, legacy_root: Path) -> str:
        return f"{datetime.now(tz=UTC):%Y-%m-%dT%H-%M-%S-%fZ}-{normalize_migration_slug(legacy_root.name)}"

    def _normalized_doc_type(self, doc_class: str) -> str:
        if doc_class == "session_log":
            return "session"
        if doc_class == "digest_daily":
            return "digest-daily"
        if doc_class == "digest_weekly":
            return "digest-weekly"
        if doc_class in {"reference_report", "reference_briefing", "reference_slide", "reference_note"}:
            return doc_class.removeprefix("reference_")
        if doc_class in {"source_web", "source_research", "source_imported", "source_legacy", "project_spec", "concept_note"}:
            return "source"
        if doc_class in {"idea_note", "draft_note", "inbox_capture", "quarantine_case", "misc_operational", "migration_note", "index_note"}:
            return "note"
        return "source"

    def _source_kind_for_classification(self, doc_class: str) -> str:
        if doc_class == "source_web":
            return "web"
        if doc_class == "source_research":
            return "research"
        if doc_class == "source_legacy":
            return "legacy"
        return "imported"

    def _family_from_subject(self, subject_ref: str) -> str:
        family, _slug = subject_ref.split(":", 1)
        if family == "person":
            return "person"
        if family == "project":
            return "project"
        if family == "concept":
            return "concept"
        if family == "decision":
            return "decision"
        raise ValueError(f"unsupported subject family: {subject_ref}")

    def _deterministic_subject_refs(self, classified: ClassifiedDocument, body: str) -> tuple[str, ...]:
        family = self._family_from_classification(classified)
        refs: list[str] = []
        if family is not None:
            for ref in classified.entity_refs:
                normalized = self._normalize_subject_ref(ref)
                if not normalized.startswith(f"{family}:"):
                    continue
                if normalized not in refs:
                    refs.append(normalized)

            if not refs:
                inferred_text = self._subject_text_from_body(body)
                if inferred_text:
                    inferred_ref = self._subject_ref_from_text(family, inferred_text)
                    if inferred_ref not in refs:
                        refs.append(inferred_ref)

            if not refs:
                target_ref = self._subject_ref_from_target_path(classified.target_path, family=family)
                if target_ref and target_ref not in refs:
                    refs.append(target_ref)

        return tuple(refs)

    def _family_from_classification(self, classified: ClassifiedDocument) -> str | None:
        doc_class = classified.doc_class
        target = Path(classified.target_path)
        if doc_class == "core_user":
            return "person"
        if doc_class == "person_profile" or target.parts[:1] == ("people",):
            return "person"
        if doc_class in {"project_state", "project_spec"} or target.parts[:1] == ("projects",):
            return "project"
        if doc_class == "concept_note" or target.parts[:1] == ("concepts",):
            return "concept"
        if doc_class == "decision_record" or target.parts[:1] == ("decisions",):
            return "decision"
        if doc_class in {"digest_daily", "digest_weekly", "session_log"}:
            return "project"

        for ref in classified.entity_refs:
            normalized = self._normalize_subject_ref(ref)
            if ":" not in normalized:
                continue
            family, _slug = normalized.split(":", 1)
            if family in {"person", "project", "concept", "decision"}:
                return family
        return None

    def _subject_ref_from_target_path(self, target_path: str, *, family: str) -> str | None:
        target = Path(target_path)
        if family == "person":
            if target.parts[:1] == ("people",) and target.stem:
                return f"person:{normalize_migration_slug(target.stem)}"
            if target.name.upper() == "USER.MD":
                return "person:user"
        if family == "project":
            if target.parts[:1] == ("projects",):
                slug = target.parent.name if target.name == "state.md" else target.stem
                return f"project:{normalize_migration_slug(slug)}"
        if family == "concept":
            if target.parts[:1] == ("concepts",):
                return f"concept:{normalize_migration_slug(target.stem)}"
            if "tools" in target.parts:
                return f"concept:{normalize_migration_slug(target.stem)}"
        if family == "decision" and target.parts[:1] == ("decisions",):
            return f"decision:{normalize_migration_slug(target.stem)}"
        return None

    def _subject_ref_from_text(self, family: str, text: str) -> str:
        return f"{family}:{normalize_migration_slug(text)}"

    def _person_entity_refs_from_text(self, text: str) -> tuple[str, ...]:
        subject_text = self._subject_text_from_body(text)
        if subject_text:
            return (self._subject_ref_from_text("person", subject_text),)
        return ("person:user",)

    def _subject_text_from_body(self, body: str) -> str | None:
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("---"):
                continue

            label_match = _SUBJECT_LABEL_RE.match(stripped)
            if label_match:
                value = self._normalize_subject_text(label_match.group("value"))
                if value is not None:
                    return value

            heading_match = _HEADING_RE.match(stripped)
            if heading_match:
                value = self._normalize_subject_text(heading_match.group("value"))
                if value is not None:
                    return value

            sentence_subject = self._subject_text_from_sentence(stripped)
            if sentence_subject is not None:
                return sentence_subject
        return None

    def _subject_text_from_sentence(self, line: str) -> str | None:
        stripped = line.lstrip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            stripped = stripped[2:].lstrip()
        match = re.match(
            r"^(?P<subject>[A-Z][\w.-]*(?:\s+[A-Z][\w.-]*){0,3})\s+"
            r"(?:is|are|was|were|remains|remained|becomes|became|stays|stayed|continues|continues to|focuses|focus|tracks|houses|supports|provides|runs|ships|works)\b",
            stripped,
        )
        if not match:
            return None
        return self._normalize_subject_text(match.group("subject"))

    def _normalize_subject_text(self, raw_text: str) -> str | None:
        text = raw_text.strip()
        if not text:
            return None
        lowered = text.casefold()
        if "—" in text:
            parts = [part.strip() for part in text.split("—") if part.strip()]
            if len(parts) > 1:
                text = parts[-1]
                lowered = text.casefold()
                if lowered.startswith("about "):
                    text = text.removeprefix("About ").removeprefix("about ").strip()
                    lowered = text.casefold()
        if " - " in text:
            parts = [part.strip() for part in text.split(" - ") if part.strip()]
            if len(parts) > 1:
                text = parts[-1]
                lowered = text.casefold()
        if lowered.startswith("about "):
            text = text.removeprefix("About ").removeprefix("about ").strip()
            lowered = text.casefold()
        if lowered in _GENERIC_SUBJECT_HEADINGS:
            return None
        return text or None

    def _kind_for_family(self, family: str) -> str:
        if family == "person":
            return "person_fact"
        if family == "project":
            return "project_update"
        if family == "concept":
            return "concept_claim"
        if family == "decision":
            return "decision"
        raise ValueError(f"unsupported family: {family}")

    def _title_from_body(self, body: str, *, family: str) -> str | None:
        subject_text = self._subject_text_from_body(body)
        _ = family
        return subject_text

    def _summary_from_body(self, body: str, *, family: str) -> str:
        _ = family
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("---") or stripped.startswith("#"):
                continue
            if _SUBJECT_LABEL_RE.match(stripped):
                continue
            return stripped
        return "migrated canonical summary."

    def _summary_from_atom(self, subject_ref: str, atom: MemoryAtom) -> str:
        summary = atom.payload.get("summary")
        _family, raw_slug = subject_ref.split(":", 1)
        display = self._title_from_atoms((atom,), fallback=canonical_title_from_subject(subject_ref), subject_ref=subject_ref)
        if isinstance(summary, str) and summary.strip():
            text = summary.strip()
            if display.lower() not in text.lower():
                text = f"{display}: {text}"
            return text + "\n"
        return f"{display}: migrated canonical summary.\n"

    def _title_from_atoms(self, atoms: tuple[MemoryAtom, ...], *, fallback: str, subject_ref: str) -> str:
        subject_slug = self._normalize_subject_ref(subject_ref).split(":", 1)[1]
        for atom in atoms:
            title = atom.payload.get("title")
            if isinstance(title, str) and title.strip():
                normalized_title = normalize_migration_slug(title)
                if normalized_title == subject_slug or subject_slug in normalized_title or normalized_title in subject_slug:
                    return title.strip()
        return fallback

    def _merge_atom_summaries(self, atoms: tuple[MemoryAtom, ...]) -> str:
        lines = self._dedupe_preserve_order(
            self._summary_from_atom(atom.subject_ref, atom).strip()
            for atom in atoms
            if self._summary_from_atom(atom.subject_ref, atom).strip()
        )
        return "\n".join(f"- {line}" for line in lines)

    def _dedupe_preserve_order(self, values: Iterable[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            lowered = value.strip().lower()
            if not lowered or lowered in seen:
                continue
            seen.add(lowered)
            ordered.append(value.strip())
        return tuple(ordered)

    def _atoms_conflict(self, left: MemoryAtom, right: MemoryAtom) -> bool:
        if left.evidence_path == right.evidence_path:
            return False
        if left.kind != right.kind:
            return False
        return self._normalized_atom_payload(left) != self._normalized_atom_payload(right)

    def _normalized_atom_payload(self, atom: MemoryAtom) -> tuple[tuple[str, str], ...]:
        normalized: list[tuple[str, str]] = []
        for key, value in sorted(atom.payload.items()):
            if value is None:
                continue
            if isinstance(value, str):
                text = " ".join(value.split()).strip().lower()
            else:
                text = json.dumps(value, sort_keys=True)
            normalized.append((key, text))
        return tuple(normalized)

    def _extract_time_ref(self, path: str) -> str | None:
        stem = Path(path).stem
        if len(stem) >= 10 and stem[4] == "-" and stem[7] == "-":
            return stem[:10]
        return None

    def _first_summary_line(self, text: str, *, fallback: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("---") or stripped.startswith("#"):
                continue
            return stripped
        return fallback

    def _extract_heading_block(self, text: str, *, default: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                continue
            if stripped:
                return stripped + "\n"
        return default + "\n"

    def _replace_section(self, rendered: str, section: str, replacement: str) -> str:
        marker = f"## {section}\n"
        if marker not in rendered:
            return rendered
        before, after = rendered.split(marker, 1)
        if "\n## " in after:
            _current, rest = after.split("\n## ", 1)
            return f"{before}{marker}{replacement.rstrip()}\n\n## {rest}"
        return f"{before}{marker}{replacement.rstrip()}\n"

    def _document_body(self, text: str) -> str:
        try:
            document = load_markdown_document(text)
        except ValueError:
            return text
        return document.body

    def _emit_progress(
        self,
        callback: Callable[[MigrationProgress], None] | None,
        *,
        phase: str,
        percent: int,
        processed: int,
        total: int,
        path: str | None = None,
        message: str | None = None,
    ) -> None:
        if callback is None:
            return
        callback(
            MigrationProgress(
                phase=phase,
                percent=max(0, min(percent, 100)),
                processed_count=max(processed, 0),
                total_count=max(total, 0),
                path=path,
                message=message,
            )
        )

    def _format_exception(self, err: Exception) -> str:
        return f"{type(err).__name__}: {err}"

    def _emit_event(
        self,
        callback: Callable[[MigrationRunEvent], None] | None,
        *,
        kind: MigrationEventKind,
        phase: str,
        processed: int,
        total: int,
        path: str | None = None,
        message: str | None = None,
        llm_classified_count: int = 0,
        llm_extracted_count: int = 0,
        fallback_classified_count: int = 0,
        fallback_extracted_count: int = 0,
        atom_count: int = 0,
        canonical_created_count: int = 0,
        written_count: int = 0,
        quarantined_count: int = 0,
        contradiction_count: int = 0,
    ) -> None:
        if callback is None:
            return
        callback(
            MigrationRunEvent(
                kind=kind,
                phase=phase,
                processed_count=max(processed, 0),
                total_count=max(total, 0),
                path=path,
                message=message,
                llm_classified_count=max(llm_classified_count, 0),
                llm_extracted_count=max(llm_extracted_count, 0),
                fallback_classified_count=max(fallback_classified_count, 0),
                fallback_extracted_count=max(fallback_extracted_count, 0),
                atom_count=max(atom_count, 0),
                canonical_created_count=max(canonical_created_count, 0),
                written_count=max(written_count, 0),
                quarantined_count=max(quarantined_count, 0),
                contradiction_count=max(contradiction_count, 0),
            )
        )

    def _phase_percent(self, processed: int, total: int, *, start: int, end: int) -> int:
        if total <= 0:
            return end
        span = max(end - start, 0)
        return start + int((processed / total) * span)
