from __future__ import annotations

import json

from dory_core.migration_types import ClassifiedDocument
from dory_core.schema import DOC_CLASSES

_CLASSIFIER_SCHEMA_NAME = "dory_migration_classification"
_EXTRACTION_SCHEMA_NAME = "dory_migration_atoms"
_DOCUMENT_SCHEMA_NAME = "dory_migration_document"
_ENTITY_RESOLUTION_SCHEMA_NAME = "dory_migration_entity_resolution"
_MIGRATION_AUDIT_SCHEMA_NAME = "dory_migration_audit"
_MIGRATION_REPAIR_SCHEMA_NAME = "dory_migration_repair"

_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "doc_class": {"type": "string", "enum": list(DOC_CLASSES)},
        "canonicality": {
            "type": "string",
            "enum": ["canonical", "evidence", "artifact", "transient"],
        },
        "target_path": {"type": "string"},
        "domain": {"type": "string", "enum": ["personal", "work", "mixed"]},
        "entity_refs": {"type": "array", "items": {"type": "string"}},
        "decision_refs": {"type": "array", "items": {"type": "string"}},
        "time_scope": {
            "type": "string",
            "enum": ["current", "historical", "timeless", "mixed"],
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "action": {
            "type": "string",
            "enum": [
                "route_final",
                "append_timeline",
                "merge_into_existing",
                "store_as_source",
                "store_as_reference",
                "quarantine",
            ],
        },
        "reason": {"type": "string"},
    },
    "required": [
        "doc_class",
        "canonicality",
        "target_path",
        "domain",
        "entity_refs",
        "decision_refs",
        "time_scope",
        "confidence",
        "action",
        "reason",
    ],
}

_EXTRACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "atoms": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "project_update",
                            "decision",
                            "preference",
                            "person_fact",
                            "concept_claim",
                            "timeline_event",
                            "goal",
                            "open_question",
                            "followup",
                        ],
                    },
                    "subject_ref": {"type": "string"},
                    "payload": {
                        "type": "object",
                        "minProperties": 1,
                        "properties": {
                            "summary": {"type": "string"},
                            "title": {"type": "string"},
                            "status": {"type": "string"},
                            "question": {"type": "string"},
                            "goal": {"type": "string"},
                            "decision": {"type": "string"},
                            "concept_kind": {"type": "string"},
                        },
                        "required": ["summary"],
                        "additionalProperties": True,
                    },
                    "evidence_path": {"type": "string"},
                    "time_ref": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                    },
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": [
                    "kind",
                    "subject_ref",
                    "payload",
                    "evidence_path",
                    "time_ref",
                    "confidence",
                ],
            },
        }
    },
    "required": ["atoms"],
}

_DOCUMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "doc_class": {"type": "string", "enum": list(DOC_CLASSES)},
        "canonicality": {
            "type": "string",
            "enum": ["canonical", "evidence", "artifact", "transient"],
        },
        "target_path": {"type": "string"},
        "domain": {"type": "string", "enum": ["personal", "work", "mixed"]},
        "entity_refs": {"type": "array", "items": {"type": "string"}},
        "decision_refs": {"type": "array", "items": {"type": "string"}},
        "time_scope": {
            "type": "string",
            "enum": ["current", "historical", "timeless", "mixed"],
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "action": {
            "type": "string",
            "enum": [
                "route_final",
                "append_timeline",
                "merge_into_existing",
                "store_as_source",
                "store_as_reference",
                "quarantine",
            ],
        },
        "reason": {"type": "string"},
        "source_quality": {"type": "string", "enum": ["strong", "mixed", "weak"]},
        "resolution_mode": {"type": "string", "enum": ["resolved", "evidence_only", "quarantine"]},
        "quarantine_reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "entity_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ref": {"type": "string"},
                    "display_name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["ref", "display_name", "aliases", "confidence"],
            },
        },
        "atoms": _EXTRACTION_SCHEMA["properties"]["atoms"],
    },
    "required": [
        "doc_class",
        "canonicality",
        "target_path",
        "domain",
        "entity_refs",
        "decision_refs",
        "time_scope",
        "confidence",
        "action",
        "reason",
        "source_quality",
        "resolution_mode",
        "quarantine_reason",
        "entity_candidates",
        "atoms",
    ],
}

_ENTITY_RESOLUTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "canonical_ref": {"type": "string"},
                    "family": {"type": "string", "enum": ["person", "project", "concept", "decision", "core"]},
                    "display_name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "member_keys": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["canonical_ref", "family", "display_name", "aliases", "member_keys"],
            },
        }
    },
    "required": ["clusters"],
}

_MIGRATION_AUDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "audits": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["pass", "review", "fail"]},
                    "summary": {"type": "string"},
                    "issues": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["path", "verdict", "summary", "issues"],
            },
        }
    },
    "required": ["audits"],
}

_MIGRATION_REPAIR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "repairs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "apply": {"type": "boolean"},
                    "summary": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "apply", "summary", "content"],
            },
        }
    },
    "required": ["repairs"],
}


def classification_schema_name() -> str:
    return _CLASSIFIER_SCHEMA_NAME


def extraction_schema_name() -> str:
    return _EXTRACTION_SCHEMA_NAME


def document_schema_name() -> str:
    return _DOCUMENT_SCHEMA_NAME


def entity_resolution_schema_name() -> str:
    return _ENTITY_RESOLUTION_SCHEMA_NAME


def migration_audit_schema_name() -> str:
    return _MIGRATION_AUDIT_SCHEMA_NAME


def migration_repair_schema_name() -> str:
    return _MIGRATION_REPAIR_SCHEMA_NAME


def classification_schema() -> dict[str, object]:
    return _CLASSIFICATION_SCHEMA


def extraction_schema() -> dict[str, object]:
    return _EXTRACTION_SCHEMA


def document_schema() -> dict[str, object]:
    return _DOCUMENT_SCHEMA


def entity_resolution_schema() -> dict[str, object]:
    return _ENTITY_RESOLUTION_SCHEMA


def migration_audit_schema() -> dict[str, object]:
    return _MIGRATION_AUDIT_SCHEMA


def migration_repair_schema() -> dict[str, object]:
    return _MIGRATION_REPAIR_SCHEMA


def build_classification_system_prompt() -> str:
    return (
        "Classify a legacy memory document into the Dory migration schema. "
        "Return JSON only. Preserve semantics. Prefer the narrowest matching doc_class. "
        "Use canonicality to indicate whether the document is canonical truth, evidence, artifact, or transient material. "
        "Do not invent entities or decisions. "
        "Use entity and decision references in canonical ref form like person:primary-user, project:rooster, concept:openclaw, decision:active-memory."
    )


def build_classification_user_prompt(*, path: str, text: str) -> str:
    return f"Document path:\n{path}\n\nDocument text:\n{text}"


def build_extraction_system_prompt() -> str:
    return (
        "Extract typed memory atoms from a legacy memory document. "
        "Return JSON only as an object with a top-level atoms array. "
        "Split mixed digests and sessions into separate atoms. "
        "Prefer explicit facts, decisions, project updates, and preferences. Do not invent facts. "
        "Each atom must use canonical subject_ref syntax like person:primary-user or project:rooster. "
        "Each payload must include a compact summary field and may include extra grounded fields like title or status. "
        "Use the provided target_path as evidence_path for every emitted atom. "
        "If the document is mostly about one known entity, reuse the provided canonical refs instead of inventing new ones. "
        "For concept_claim atoms, include title when grounded. For project_update and decision atoms, include title when obvious from the document. "
        "If nothing meets the threshold, return an empty atoms array."
    )


def build_document_extraction_system_prompt() -> str:
    return (
        "Read one legacy memory document and produce a strict structured migration result. "
        "Return JSON only. "
        "Your job is to understand the file, identify grounded entities and claims, and decide whether the document is "
        "safe to integrate into resolved memory right now. "
        "Use `resolution_mode=resolved` only when the document clearly supports one or more grounded entities and claims "
        "that are safe to compile into canonical memory. "
        "Use `resolution_mode=evidence_only` when the file should be preserved as evidence but should not directly drive canonical synthesis yet. "
        "Use `resolution_mode=quarantine` for junk, ambiguity, weak speculation, generated artifacts, or low-quality material. "
        "Do not invent entities, aliases, or claims. "
        "For each entity candidate, use canonical refs like person:primary-user, project:openclaw, concept:vector-search. "
        "For each atom, include compact grounded payloads and reuse the resolved refs. "
        "When a file is speculative, mixed, ambiguous, or low quality, prefer evidence_only or quarantine rather than forcing canonical truth."
    )


def build_entity_resolution_user_prompt(
    *,
    family: str,
    candidates: list[dict[str, object]],
    existing_entities: list[dict[str, object]],
) -> str:
    return (
        f"Entity family:\n{family}\n\n"
        f"Candidate mentions:\n{json.dumps(candidates, indent=2, sort_keys=True)}\n\n"
        f"Existing registry entities:\n{json.dumps(existing_entities, indent=2, sort_keys=True)}\n\n"
        "Required output:\n"
        "- Put every candidate mention key into exactly one cluster.\n"
        "- Reuse an existing canonical_ref when it is clearly the right entity.\n"
        "- Otherwise choose the best canonical_ref from the candidate refs.\n"
        "- Keep aliases grounded in the mentions or existing registry.\n"
    )


def build_entity_resolution_system_prompt() -> str:
    return (
        "Resolve migration entity candidates across a corpus into canonical entity clusters. "
        "Return JSON only. "
        "Merge only when the candidates clearly refer to the same real entity. "
        "Prefer existing canonical refs when they already fit. "
        "Do not merge across families. "
        "Do not invent entities that are not grounded in the candidate list or existing registry options."
    )


def build_migration_audit_system_prompt() -> str:
    return (
        "Audit generated migration pages for correctness, evidence quality, overclaiming, bad routing, and contradiction handling. "
        "Return JSON only. "
        "Mark a page as fail only when the generated page is clearly unsafe or misleading. "
        "Mark review when the page looks plausible but weak, lossy, or under-evidenced."
    )


def build_migration_repair_system_prompt() -> str:
    return (
        "Repair generated migration pages using only the provided grounded evidence, claim history, and claim events. "
        "Return JSON only. "
        "Keep the page path fixed. Preserve valid frontmatter. "
        "Be more conservative when evidence is thin, ambiguous, or weak. "
        "Do not invent claims, entities, or evidence paths. "
        "If a page should stay unchanged, return apply=false with the original content."
    )


def build_migration_audit_user_prompt(*, pages: list[dict[str, object]]) -> str:
    return (
        f"Generated pages:\n{json.dumps(pages, indent=2, sort_keys=True)}\n\n"
        "Required output:\n"
        "- Audit every page once.\n"
        "- Use short concrete issue strings.\n"
        "- Focus on migration quality, not style.\n"
    )


def build_migration_repair_user_prompt(*, pages: list[dict[str, object]]) -> str:
    return (
        f"Flagged generated pages:\n{json.dumps(pages, indent=2, sort_keys=True)}\n\n"
        "Required output:\n"
        "- Return one repair item for every provided page.\n"
        "- Keep current grounded facts that are supported by the provided claims and events.\n"
        "- If evidence is weak, narrow the summary and move uncertainty into explicit caveats or open questions.\n"
        "- Keep evidence paths grounded in the supplied claims/events.\n"
    )


def build_extraction_user_prompt(
    *,
    path: str,
    text: str,
    classified: ClassifiedDocument | None = None,
) -> str:
    doc_class = classified.doc_class if classified is not None else None
    target_path = classified.target_path if classified is not None else None
    entity_refs = classified.entity_refs if classified is not None else ()
    decision_refs = classified.decision_refs if classified is not None else ()
    guidance = _build_extraction_guidance(
        doc_class=doc_class,
        entity_refs=entity_refs,
        decision_refs=decision_refs,
        target_path=target_path,
    )
    return f"Document path:\n{path}\n\n{guidance}\n\nDocument text:\n{text}"


def build_document_extraction_user_prompt(*, path: str, text: str) -> str:
    return (
        f"Document path:\n{path}\n\n"
        "Required output:\n"
        "- classify the document\n"
        "- emit candidate entities with aliases\n"
        "- emit grounded atoms with canonical subject refs\n"
        "- decide whether the document is resolved, evidence_only, or quarantine\n"
        "- explain the decision in `reason`\n\n"
        f"Document text:\n{text}"
    )


def _build_extraction_guidance(
    *,
    doc_class: str | None,
    entity_refs: tuple[str, ...],
    decision_refs: tuple[str, ...],
    target_path: str | None,
) -> str:
    normalized_doc_class = doc_class or "unknown"
    guidance_lines = [
        f"Known doc_class: {normalized_doc_class}",
        f"Use this evidence_path for every atom: {target_path or 'same document path'}",
        f"Known entity refs: {', '.join(entity_refs) if entity_refs else 'none'}",
        f"Known decision refs: {', '.join(decision_refs) if decision_refs else 'none'}",
        "Keep payload.summary concrete and one or two sentences max.",
    ]
    atom_hints = _atom_hints_for_doc_class(normalized_doc_class)
    if atom_hints:
        guidance_lines.append(f"Prefer these atom kinds: {', '.join(atom_hints)}")
    return "\n".join(guidance_lines)


def _atom_hints_for_doc_class(doc_class: str) -> tuple[str, ...]:
    if doc_class in {"core_user", "person_profile"}:
        return ("person_fact", "preference", "goal")
    if doc_class in {"project_state", "project_spec"}:
        return ("project_update", "decision", "followup", "timeline_event")
    if doc_class in {"digest_daily", "digest_weekly", "session_log"}:
        return ("project_update", "decision", "preference", "person_fact", "timeline_event", "followup")
    if doc_class in {"concept_note", "source_web", "source_research", "source_imported", "source_legacy"}:
        return ("concept_claim", "decision", "project_update", "open_question")
    if doc_class == "decision_record":
        return ("decision", "timeline_event")
    return ()
