from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from dory_core.migration_normalize import normalize_migration_slug
from dory_core.schema import DOC_CLASSES
from dory_core.migration_types import (
    ClassifiedDocument,
    ExtractedMigrationDocument,
    MemoryAtom,
    MigrationPageAudit,
    MigrationPageRepair,
    MigrationEntityCandidate,
    MigrationEntityCluster,
    MigrationEntityMention,
)
from dory_core.migration_prompts import (
    build_classification_system_prompt,
    build_classification_user_prompt,
    build_document_extraction_system_prompt,
    build_document_extraction_user_prompt,
    build_entity_resolution_system_prompt,
    build_entity_resolution_user_prompt,
    build_extraction_system_prompt,
    build_extraction_user_prompt,
    build_migration_audit_system_prompt,
    build_migration_audit_user_prompt,
    build_migration_repair_system_prompt,
    build_migration_repair_user_prompt,
    classification_schema,
    classification_schema_name,
    document_schema,
    document_schema_name,
    entity_resolution_schema,
    entity_resolution_schema_name,
    extraction_schema,
    extraction_schema_name,
    migration_audit_schema,
    migration_audit_schema_name,
    migration_repair_schema,
    migration_repair_schema_name,
)


class MigrationLLMClient(Protocol):
    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> Any:
        ...


@dataclass(frozen=True, slots=True)
class MigrationLLM:
    client: MigrationLLMClient

    def extract_document(self, *, path: str, text: str) -> ExtractedMigrationDocument:
        payload = self.client.generate_json(
            system_prompt=build_document_extraction_system_prompt(),
            user_prompt=build_document_extraction_user_prompt(path=path, text=text),
            schema_name=document_schema_name(),
            schema=document_schema(),
        )
        if not isinstance(payload, dict):
            raise ValueError("document extraction payload must be an object")
        return parse_document_response(payload)

    def classify_document(self, *, path: str, text: str) -> ClassifiedDocument:
        payload = self.client.generate_json(
            system_prompt=build_classification_system_prompt(),
            user_prompt=build_classification_user_prompt(path=path, text=text),
            schema_name=classification_schema_name(),
            schema=classification_schema(),
        )
        if not isinstance(payload, dict):
            raise ValueError("classification payload must be an object")
        return parse_classification_response(payload)

    def extract_atoms(
        self,
        *,
        path: str,
        text: str,
        classified: ClassifiedDocument | None = None,
    ) -> tuple[MemoryAtom, ...]:
        context = ExtractionContext.from_inputs(path=path, classified=classified)
        payload = self.client.generate_json(
            system_prompt=build_extraction_system_prompt(),
            user_prompt=build_extraction_user_prompt(path=path, text=text, classified=classified),
            schema_name=extraction_schema_name(),
            schema=extraction_schema(),
        )
        items = _extract_atom_items(payload)
        if not items:
            return ()
        return parse_extraction_response(items, context=context, strict=False)

    def resolve_entity_mentions(
        self,
        *,
        family: str,
        candidates: tuple[MigrationEntityMention, ...],
        existing_entities: tuple[dict[str, object], ...] = (),
    ) -> tuple[MigrationEntityCluster, ...]:
        payload = self.client.generate_json(
            system_prompt=build_entity_resolution_system_prompt(),
            user_prompt=build_entity_resolution_user_prompt(
                family=family,
                candidates=[candidate.to_dict() for candidate in candidates],
                existing_entities=list(existing_entities),
            ),
            schema_name=entity_resolution_schema_name(),
            schema=entity_resolution_schema(),
        )
        if not isinstance(payload, dict):
            raise ValueError("entity resolution payload must be an object")
        return parse_entity_resolution_response(payload, expected_family=family)

    def audit_migration_pages(self, *, pages: tuple[dict[str, object], ...]) -> tuple[MigrationPageAudit, ...]:
        payload = self.client.generate_json(
            system_prompt=build_migration_audit_system_prompt(),
            user_prompt=build_migration_audit_user_prompt(pages=list(pages)),
            schema_name=migration_audit_schema_name(),
            schema=migration_audit_schema(),
        )
        if not isinstance(payload, dict):
            raise ValueError("migration audit payload must be an object")
        return parse_migration_audit_response(payload)

    def repair_migration_pages(self, *, pages: tuple[dict[str, object], ...]) -> tuple[MigrationPageRepair, ...]:
        payload = self.client.generate_json(
            system_prompt=build_migration_repair_system_prompt(),
            user_prompt=build_migration_repair_user_prompt(pages=list(pages)),
            schema_name=migration_repair_schema_name(),
            schema=migration_repair_schema(),
        )
        if not isinstance(payload, dict):
            raise ValueError("migration repair payload must be an object")
        return parse_migration_repair_response(payload)


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    evidence_path: str
    entity_refs: tuple[str, ...] = ()
    decision_refs: tuple[str, ...] = ()

    @classmethod
    def from_inputs(
        cls,
        *,
        path: str,
        classified: ClassifiedDocument | None,
    ) -> "ExtractionContext":
        if classified is None:
            return cls(evidence_path=path)
        return cls(
            evidence_path=classified.target_path or path,
            entity_refs=classified.entity_refs,
            decision_refs=classified.decision_refs,
        )


def parse_classification_response(payload: dict[str, Any]) -> ClassifiedDocument:
    return ClassifiedDocument(
        doc_class=_require_enum(payload, "doc_class", set(DOC_CLASSES)),
        canonicality=_require_enum(payload, "canonicality", {"canonical", "evidence", "artifact", "transient"}),
        target_path=_require_str(payload, "target_path"),
        domain=_require_enum(payload, "domain", {"personal", "work", "mixed"}),
        entity_refs=_normalize_refs(_require_str_tuple(payload, "entity_refs")),
        decision_refs=_normalize_refs(_require_str_tuple(payload, "decision_refs")),
        time_scope=_require_enum(payload, "time_scope", {"current", "historical", "timeless", "mixed"}),
        confidence=_require_enum(payload, "confidence", {"high", "medium", "low"}),
        action=_require_enum(
            payload,
            "action",
            {
                "route_final",
                "append_timeline",
                "merge_into_existing",
                "store_as_source",
                "store_as_reference",
                "quarantine",
            },
        ),
        reason=_require_str(payload, "reason"),
    )


def parse_document_response(payload: dict[str, Any]) -> ExtractedMigrationDocument:
    classified = parse_classification_response(payload)
    entity_candidates = _parse_entity_candidates(payload.get("entity_candidates"))
    atoms = parse_extraction_response(
        _extract_atom_items(payload),
        context=ExtractionContext.from_inputs(path=classified.target_path, classified=classified),
        strict=False,
    )
    source_quality = _require_enum(payload, "source_quality", {"strong", "mixed", "weak"})
    resolution_mode = _require_enum(payload, "resolution_mode", {"resolved", "evidence_only", "quarantine"})
    quarantine_reason = _optional_str(payload, "quarantine_reason")
    if resolution_mode == "quarantine" and quarantine_reason is None:
        quarantine_reason = classified.reason
    return ExtractedMigrationDocument(
        classified=classified,
        source_quality=source_quality,
        resolution_mode=resolution_mode,
        quarantine_reason=quarantine_reason,
        entity_candidates=entity_candidates,
        atoms=atoms,
    )


def parse_extraction_response(
    items: list[dict[str, Any]],
    *,
    context: ExtractionContext | None = None,
    strict: bool = True,
) -> tuple[MemoryAtom, ...]:
    parsed: list[MemoryAtom] = []
    for item in items:
        try:
            parsed.append(_parse_extraction_atom(item, context=context))
        except ValueError:
            if strict:
                raise
            continue
    return tuple(parsed)


def parse_entity_resolution_response(
    payload: dict[str, Any],
    *,
    expected_family: str,
) -> tuple[MigrationEntityCluster, ...]:
    raw_clusters = payload.get("clusters")
    if not isinstance(raw_clusters, list):
        raise ValueError("entity resolution payload field must be a list: clusters")
    clusters: list[MigrationEntityCluster] = []
    seen_member_keys: set[str] = set()
    for item in raw_clusters:
        if not isinstance(item, dict):
            raise ValueError("entity cluster must be an object")
        family = _require_enum(item, "family", {"person", "project", "concept", "decision", "core"})
        if family != expected_family:
            raise ValueError(f"entity cluster family mismatch: {family} != {expected_family}")
        member_keys = _require_str_tuple(item, "member_keys")
        if not member_keys:
            raise ValueError("entity cluster must contain at least one member key")
        for key in member_keys:
            if key in seen_member_keys:
                raise ValueError(f"entity cluster member key repeated across clusters: {key}")
            seen_member_keys.add(key)
        clusters.append(
            MigrationEntityCluster(
                canonical_ref=_normalize_ref(_require_str(item, "canonical_ref")),
                family=family,
                display_name=_require_str(item, "display_name"),
                aliases=_require_str_tuple(item, "aliases"),
                member_keys=member_keys,
            )
        )
    return tuple(clusters)


def parse_migration_audit_response(payload: dict[str, Any]) -> tuple[MigrationPageAudit, ...]:
    raw_audits = payload.get("audits")
    if not isinstance(raw_audits, list):
        raise ValueError("migration audit payload field must be a list: audits")
    audits: list[MigrationPageAudit] = []
    for item in raw_audits:
        if not isinstance(item, dict):
            raise ValueError("migration audit item must be an object")
        audits.append(
            MigrationPageAudit(
                path=_require_str(item, "path"),
                verdict=_require_enum(item, "verdict", {"pass", "review", "fail"}),  # type: ignore[arg-type]
                summary=_require_str(item, "summary"),
                issues=_require_str_tuple(item, "issues"),
            )
        )
    return tuple(audits)


def parse_migration_repair_response(payload: dict[str, Any]) -> tuple[MigrationPageRepair, ...]:
    raw_repairs = payload.get("repairs")
    if not isinstance(raw_repairs, list):
        raise ValueError("migration repair payload field must be a list: repairs")
    repairs: list[MigrationPageRepair] = []
    for item in raw_repairs:
        if not isinstance(item, dict):
            raise ValueError("migration repair item must be an object")
        repairs.append(
            MigrationPageRepair(
                path=_require_str(item, "path"),
                apply=_require_bool(item, "apply"),
                summary=_require_str(item, "summary"),
                content=_require_str(item, "content"),
            )
        )
    return tuple(repairs)


def _extract_atom_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        atoms = payload.get("atoms")
        if isinstance(atoms, list):
            return [item for item in atoms if isinstance(item, dict)]
    return []


def _parse_entity_candidates(payload: Any) -> tuple[MigrationEntityCandidate, ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise ValueError("entity_candidates must be a list")
    candidates: list[MigrationEntityCandidate] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("entity candidate must be an object")
        aliases = _require_str_tuple(item, "aliases")
        candidates.append(
            MigrationEntityCandidate(
                ref=_normalize_ref(_require_str(item, "ref")),
                display_name=_require_str(item, "display_name"),
                aliases=aliases,
                confidence=_require_enum(item, "confidence", {"high", "medium", "low"}),
            )
        )
    return tuple(candidates)


def _parse_extraction_atom(
    item: dict[str, Any],
    *,
    context: ExtractionContext | None,
) -> MemoryAtom:
    kind = _require_enum(
        item,
        "kind",
        {
            "project_update",
            "decision",
            "preference",
            "person_fact",
            "concept_claim",
            "timeline_event",
            "goal",
            "open_question",
            "followup",
        },
    )
    return MemoryAtom(
        kind=kind,
        subject_ref=_normalize_atom_ref(_require_str(item, "subject_ref"), kind=kind, context=context),
        payload=_require_payload(item),
        evidence_path=_require_evidence_path(item, context=context),
        time_ref=_optional_str(item, "time_ref"),
        confidence=_require_enum(item, "confidence", {"high", "medium", "low"}),
    )


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"migration payload missing string field: {key}")
    return value.strip()


def _require_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"migration payload missing boolean field: {key}")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"migration payload field must be string or null: {key}")
    return value.strip()


def _require_enum(payload: dict[str, Any], key: str, allowed: set[str]) -> str:
    value = _require_str(payload, key)
    if value not in allowed:
        raise ValueError(f"migration payload field {key} has invalid value: {value}")
    return value


def _require_str_tuple(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"migration payload field must be a list of strings: {key}")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"migration payload field contains non-string item: {key}")
        items.append(item.strip())
    return tuple(items)


def _require_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("migration payload field missing object: payload")
    normalized = {str(key): value for key, value in payload.items() if _payload_value_present(value)}
    if "summary" not in normalized:
        derived_summary = _derive_payload_summary(normalized)
        if derived_summary is not None:
            normalized["summary"] = derived_summary
    if not normalized:
        raise ValueError("migration payload object must not be empty")
    return normalized


def _require_evidence_path(item: dict[str, Any], *, context: ExtractionContext | None) -> str:
    if context is not None and context.evidence_path.strip():
        return context.evidence_path.strip()
    return _require_str(item, "evidence_path")


def _payload_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _normalize_refs(refs: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_normalize_ref(ref) for ref in refs)


def _normalize_atom_ref(raw_ref: str, *, kind: str, context: ExtractionContext | None) -> str:
    normalized = _normalize_ref(raw_ref)
    if ":" in normalized:
        return normalized

    preferred_refs = _preferred_refs_for_kind(kind=kind, context=context)
    if len(preferred_refs) == 1:
        return preferred_refs[0]

    family = _default_family_for_kind(kind)
    if family is None:
        raise ValueError(f"cannot infer subject ref family for kind: {kind}")
    return f"{family}:{normalize_migration_slug(normalized)}"


def _normalize_ref(ref: str) -> str:
    raw = ref.strip()
    if not raw:
        raise ValueError("migration ref must not be empty")
    if ":" in raw:
        family, slug = raw.split(":", 1)
        return f"{_normalize_family(family)}:{normalize_migration_slug(slug)}"
    path = raw.strip("/").replace("\\", "/")
    if "/" in path:
        family, slug = path.split("/", 1)
        return f"{_normalize_family(family)}:{normalize_migration_slug(slug)}"
    return raw


def _preferred_refs_for_kind(*, kind: str, context: ExtractionContext | None) -> tuple[str, ...]:
    if context is None:
        return ()
    family = _default_family_for_kind(kind)
    if family == "decision":
        refs = context.decision_refs
    else:
        refs = context.entity_refs
    if family is None:
        return refs
    return tuple(ref for ref in refs if ref.startswith(f"{family}:"))


def _default_family_for_kind(kind: str) -> str | None:
    if kind in {"person_fact", "preference", "goal"}:
        return "person"
    if kind == "project_update":
        return "project"
    if kind == "concept_claim":
        return "concept"
    if kind == "decision":
        return "decision"
    return None


def _normalize_family(family: str) -> str:
    normalized = family.strip().lower()
    return {
        "people": "person",
        "person": "person",
        "projects": "project",
        "project": "project",
        "concepts": "concept",
        "concept": "concept",
        "decisions": "decision",
        "decision": "decision",
    }.get(normalized, normalized)


def _derive_payload_summary(payload: dict[str, Any]) -> str | None:
    for key in ("decision", "goal", "question", "title"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
