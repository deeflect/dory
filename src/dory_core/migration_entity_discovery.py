"""Discover canonical entities across the migrated corpus.

Two-phase LLM pass over the batches produced by ``migration_batching``:

1. **Map**: for each ≤150K batch, the LLM returns the durable entities
   it sees — people, projects, concepts, decisions — with aliases,
   evidence paths, and a one-liner grounded in the batch text.
2. **Reduce**: all map outputs are fed to a single large-context call
   that merges duplicates (clawsy/Clawzy/claws-ai → one entity),
   canonicalizes slugs, and returns the final entity roster.

Output: ``.dory/entities.json`` — the ground truth every later pass
references.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Protocol, Sequence

from dory_core.fs import atomic_write_text
from dory_core.llm.openrouter import OpenRouterClient, OpenRouterProviderError
from dory_core.migration_batching import Batch


EntityFamily = Literal["person", "project", "concept", "decision"]
StatusSignal = Literal["active", "paused", "done", "stale", "unknown"]

_ALLOWED_FAMILIES: frozenset[str] = frozenset({"person", "project", "concept", "decision"})
_ALLOWED_STATUS: frozenset[str] = frozenset({"active", "paused", "done", "stale", "unknown"})

_MAX_SNIPPET_CHARS_PER_FILE = 6000


@dataclass(frozen=True, slots=True)
class BatchEntity:
    slug: str
    family: EntityFamily
    aliases: tuple[str, ...]
    one_liner: str
    status_signal: StatusSignal
    evidence_paths: tuple[str, ...]
    mention_count: int
    batch_label: str


@dataclass(frozen=True, slots=True)
class CanonicalEntity:
    slug: str
    family: EntityFamily
    aliases: tuple[str, ...]
    one_liner: str
    status_signal: StatusSignal
    evidence_paths: tuple[str, ...]
    mention_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "family": self.family,
            "aliases": list(self.aliases),
            "one_liner": self.one_liner,
            "status_signal": self.status_signal,
            "evidence_paths": list(self.evidence_paths),
            "mention_count": self.mention_count,
        }


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    batch_count: int
    batches_processed: int
    batches_failed: int
    raw_entity_count: int
    canonical_entities: list[CanonicalEntity] = field(default_factory=list)


_MAP_SYSTEM_PROMPT = (
    "You extract durable entities from a batch of personal-memory files.\n"
    "Durable entities are people, projects, concepts, or decisions that a "
    "reader would still care about months from now. Ephemeral TODOs, session "
    "banter, and one-off notes are NOT entities.\n\n"
    "Rules:\n"
    "1. slug must be lowercase kebab-case, stable (use the most common form).\n"
    "2. family is exactly one of: person, project, concept, decision.\n"
    "3. aliases include variant spellings/capitalizations/shortened forms "
    "seen in the batch. Never invent aliases.\n"
    "4. one_liner is ONE sentence, grounded in the batch — not a summary "
    "of what an entity *could* be. If you don't have enough signal, skip it.\n"
    "5. status_signal reflects what the batch says about current state "
    "(active / paused / done / stale / unknown).\n"
    "6. evidence_paths are exact file paths from the batch where the entity "
    "appears. Never fabricate paths.\n"
    "7. mention_count is the number of DISTINCT files where the entity appears.\n"
    "8. If the batch has no durable entities, return an empty array.\n"
    "Never invent. When unsure, skip."
)

_MAP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "slug": {"type": "string"},
                    "family": {"type": "string", "enum": list(_ALLOWED_FAMILIES)},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "one_liner": {"type": "string"},
                    "status_signal": {"type": "string", "enum": list(_ALLOWED_STATUS)},
                    "evidence_paths": {"type": "array", "items": {"type": "string"}},
                    "mention_count": {"type": "integer", "minimum": 1},
                },
                "required": [
                    "slug",
                    "family",
                    "aliases",
                    "one_liner",
                    "status_signal",
                    "evidence_paths",
                    "mention_count",
                ],
            },
        }
    },
    "required": ["entities"],
}

_REDUCE_SYSTEM_PROMPT = (
    "You merge per-batch entity lists into a single canonical roster.\n"
    "Two entities from different batches are the SAME entity when:\n"
    "- slugs are identical after normalization, or\n"
    "- aliases overlap significantly, or\n"
    "- one_liners describe the same thing (same person, same project).\n\n"
    "For each canonical entity, produce:\n"
    "- canonical_slug: the best lowercase kebab-case form\n"
    "- family: person | project | concept | decision (if batches disagree, "
    "pick the most specific that fits)\n"
    "- aliases: UNION of all aliases seen (deduplicated)\n"
    "- one_liner: clearest synthesis across batches — still one sentence, "
    "still grounded in the evidence\n"
    "- status_signal: the most recent / most evidence-backed status\n"
    "- evidence_paths: UNION of all paths (deduplicated)\n"
    "- mention_count: sum across batches\n\n"
    "Rules:\n"
    "- Do NOT lose entities in the merge. If a batch entity has no match, keep it.\n"
    "- Do NOT invent aliases not present in input.\n"
    "- Slugs must be lowercase kebab-case.\n"
    "- Prefer shorter, more-used aliases as the canonical slug."
)

_REDUCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "canonical_slug": {"type": "string"},
                    "family": {"type": "string", "enum": list(_ALLOWED_FAMILIES)},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "one_liner": {"type": "string"},
                    "status_signal": {"type": "string", "enum": list(_ALLOWED_STATUS)},
                    "evidence_paths": {"type": "array", "items": {"type": "string"}},
                    "mention_count": {"type": "integer", "minimum": 1},
                },
                "required": [
                    "canonical_slug",
                    "family",
                    "aliases",
                    "one_liner",
                    "status_signal",
                    "evidence_paths",
                    "mention_count",
                ],
            },
        }
    },
    "required": ["entities"],
}


class EntityDiscoveryProgress(Protocol):
    def __call__(self, *, phase: str, index: int, total: int, label: str) -> None: ...


def discover_entities(
    corpus_root: Path,
    batches: Sequence[Batch],
    *,
    client: OpenRouterClient,
    progress: EntityDiscoveryProgress | None = None,
) -> DiscoveryReport:
    """Run the map + reduce phases and return the canonical entity roster."""
    raw_entities: list[BatchEntity] = []
    failed = 0

    for index, batch in enumerate(batches, start=1):
        if progress is not None:
            progress(phase="map", index=index, total=len(batches), label=batch.label)
        batch_entities = _run_map(client, corpus_root, batch)
        if batch_entities is None:
            failed += 1
            continue
        raw_entities.extend(batch_entities)

    if progress is not None:
        progress(phase="reduce", index=1, total=1, label="all-batches")
    canonical = _run_reduce(client, raw_entities) if raw_entities else []

    return DiscoveryReport(
        batch_count=len(batches),
        batches_processed=len(batches) - failed,
        batches_failed=failed,
        raw_entity_count=len(raw_entities),
        canonical_entities=canonical,
    )


def write_entities(path: Path, report: DiscoveryReport) -> None:
    payload = {
        "batch_count": report.batch_count,
        "batches_processed": report.batches_processed,
        "batches_failed": report.batches_failed,
        "raw_entity_count": report.raw_entity_count,
        "entities": [entity.to_dict() for entity in report.canonical_entities],
    }
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_map(
    client: OpenRouterClient,
    corpus_root: Path,
    batch: Batch,
) -> list[BatchEntity] | None:
    user_prompt = _build_map_prompt(corpus_root, batch)
    try:
        payload = client.generate_json(
            system_prompt=_MAP_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema_name="dory_entity_discovery_batch",
            schema=_MAP_SCHEMA,
        )
    except OpenRouterProviderError:
        return None
    return _parse_map_entities(payload, batch_label=batch.label)


def _run_reduce(
    client: OpenRouterClient,
    raw: list[BatchEntity],
) -> list[CanonicalEntity]:
    user_prompt = _build_reduce_prompt(raw)
    try:
        payload = client.generate_json(
            system_prompt=_REDUCE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema_name="dory_entity_discovery_canonical",
            schema=_REDUCE_SCHEMA,
        )
    except OpenRouterProviderError:
        # If reduce fails, fall back to concatenating batches without dedup.
        return _fallback_canonicalize(raw)
    canonical = _parse_canonical_entities(payload)
    if not canonical:
        return _fallback_canonicalize(raw)
    return canonical


def _build_map_prompt(corpus_root: Path, batch: Batch) -> str:
    sections: list[str] = [
        f"Batch label: {batch.label}",
        f"Files in this batch: {batch.file_count}",
        "",
        "=== FILES ===",
    ]
    for batch_file in batch.files:
        absolute = corpus_root / batch_file.relative_path
        try:
            text = absolute.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        truncated = text[:_MAX_SNIPPET_CHARS_PER_FILE]
        sections.append(f"\n--- {batch_file.relative_path.as_posix()} ---")
        sections.append(truncated)
    return "\n".join(sections)


def _build_reduce_prompt(raw: Iterable[BatchEntity]) -> str:
    lines: list[str] = [
        "Per-batch entity lists (one object per line).",
        "Merge duplicates. Return the canonical roster.",
        "",
    ]
    for entity in raw:
        lines.append(
            json.dumps(
                {
                    "batch": entity.batch_label,
                    "slug": entity.slug,
                    "family": entity.family,
                    "aliases": list(entity.aliases),
                    "one_liner": entity.one_liner,
                    "status_signal": entity.status_signal,
                    "evidence_paths": list(entity.evidence_paths),
                    "mention_count": entity.mention_count,
                },
                sort_keys=True,
            )
        )
    return "\n".join(lines)


def _parse_map_entities(payload: Any, *, batch_label: str) -> list[BatchEntity]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("entities")
    if not isinstance(raw, list):
        return []
    entities: list[BatchEntity] = []
    for item in raw:
        entity = _coerce_batch_entity(item, batch_label=batch_label)
        if entity is not None:
            entities.append(entity)
    return entities


def _parse_canonical_entities(payload: Any) -> list[CanonicalEntity]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("entities")
    if not isinstance(raw, list):
        return []
    canonical: list[CanonicalEntity] = []
    for item in raw:
        entity = _coerce_canonical_entity(item)
        if entity is not None:
            canonical.append(entity)
    return canonical


def _coerce_batch_entity(item: Any, *, batch_label: str) -> BatchEntity | None:
    if not isinstance(item, dict):
        return None
    slug = item.get("slug")
    family = item.get("family")
    aliases = item.get("aliases")
    one_liner = item.get("one_liner")
    status_signal = item.get("status_signal")
    evidence_paths = item.get("evidence_paths")
    mention_count = item.get("mention_count")
    if not isinstance(slug, str) or not slug.strip():
        return None
    if family not in _ALLOWED_FAMILIES:
        return None
    if not isinstance(aliases, list):
        return None
    if not isinstance(one_liner, str):
        return None
    if status_signal not in _ALLOWED_STATUS:
        return None
    if not isinstance(evidence_paths, list):
        return None
    if not isinstance(mention_count, int) or mention_count < 1:
        return None
    return BatchEntity(
        slug=_normalize_slug(slug),
        family=family,  # type: ignore[arg-type]
        aliases=tuple(a.strip() for a in aliases if isinstance(a, str) and a.strip()),
        one_liner=one_liner.strip(),
        status_signal=status_signal,  # type: ignore[arg-type]
        evidence_paths=tuple(p.strip() for p in evidence_paths if isinstance(p, str) and p.strip()),
        mention_count=mention_count,
        batch_label=batch_label,
    )


def _coerce_canonical_entity(item: Any) -> CanonicalEntity | None:
    if not isinstance(item, dict):
        return None
    slug = item.get("canonical_slug")
    family = item.get("family")
    aliases = item.get("aliases")
    one_liner = item.get("one_liner")
    status_signal = item.get("status_signal")
    evidence_paths = item.get("evidence_paths")
    mention_count = item.get("mention_count")
    if not isinstance(slug, str) or not slug.strip():
        return None
    if family not in _ALLOWED_FAMILIES:
        return None
    if not isinstance(aliases, list):
        return None
    if not isinstance(one_liner, str):
        return None
    if status_signal not in _ALLOWED_STATUS:
        return None
    if not isinstance(evidence_paths, list):
        return None
    if not isinstance(mention_count, int) or mention_count < 1:
        return None
    return CanonicalEntity(
        slug=_normalize_slug(slug),
        family=family,  # type: ignore[arg-type]
        aliases=tuple(sorted({a.strip() for a in aliases if isinstance(a, str) and a.strip()})),
        one_liner=one_liner.strip(),
        status_signal=status_signal,  # type: ignore[arg-type]
        evidence_paths=tuple(sorted({p.strip() for p in evidence_paths if isinstance(p, str) and p.strip()})),
        mention_count=mention_count,
    )


def _fallback_canonicalize(raw: Iterable[BatchEntity]) -> list[CanonicalEntity]:
    """Merge by exact slug match; no alias/fuzzy dedup."""
    grouped: dict[str, list[BatchEntity]] = {}
    for entity in raw:
        grouped.setdefault(entity.slug, []).append(entity)
    canonical: list[CanonicalEntity] = []
    for slug, entities in grouped.items():
        aliases: set[str] = set()
        paths: set[str] = set()
        mentions = 0
        family = entities[0].family
        one_liner = entities[0].one_liner
        status = entities[0].status_signal
        for entity in entities:
            aliases.update(entity.aliases)
            paths.update(entity.evidence_paths)
            mentions += entity.mention_count
        canonical.append(
            CanonicalEntity(
                slug=slug,
                family=family,
                aliases=tuple(sorted(aliases)),
                one_liner=one_liner,
                status_signal=status,
                evidence_paths=tuple(sorted(paths)),
                mention_count=mentions,
            )
        )
    canonical.sort(key=lambda e: (-e.mention_count, e.slug))
    return canonical


def _normalize_slug(raw: str) -> str:
    cleaned = raw.strip().lower().replace("_", "-").replace(" ", "-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def format_discovery_summary(report: DiscoveryReport) -> dict[str, Any]:
    by_family: dict[str, int] = {}
    for entity in report.canonical_entities:
        by_family[entity.family] = by_family.get(entity.family, 0) + 1
    return {
        "batches_total": report.batch_count,
        "batches_processed": report.batches_processed,
        "batches_failed": report.batches_failed,
        "raw_entities_from_map": report.raw_entity_count,
        "canonical_entities": len(report.canonical_entities),
        "by_family": dict(sorted(by_family.items(), key=lambda kv: -kv[1])),
    }
