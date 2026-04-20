"""Classify and promote files in ``ideas/``.

After entity synthesis, every idea file is read by the LLM alongside
the canonical entity roster. The LLM classifies each idea as one of:

- ``stay``: keep in ``ideas/``
- ``promote_to_concept``: this idea is a durable concept (mature, cross-
  references multiple projects, or general mental model). Move to
  ``concepts/<slug>.md``.
- ``promote_to_project``: this idea has a spec, domain, or shipped
  artifact. Move to ``projects/<slug>/state.md``.
- ``merge_with_entity``: the idea is evidence for an existing entity
  (from Pass B's entities.json). File stays in ideas/ but gets
  backlinked; a note is added to the entity page (done by later passes).

Batched in chunks of 30 ideas per call with strict JSON schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence

from dory_core.frontmatter import (
    dump_markdown_document,
    load_markdown_document,
    merge_frontmatter,
)
from dory_core.fs import atomic_write_text, resolve_corpus_target
from dory_core.llm.openrouter import OpenRouterClient, OpenRouterProviderError
from dory_core.metadata import normalize_frontmatter
from dory_core.migration_entity_discovery import CanonicalEntity


Classification = Literal[
    "stay",
    "promote_to_concept",
    "promote_to_project",
    "merge_with_entity",
]

_BATCH_SIZE = 30
_MAX_SNIPPET_CHARS = 4000


@dataclass(frozen=True, slots=True)
class IdeaDecision:
    source_path: str
    classification: Classification
    target_slug: str | None
    rationale: str


@dataclass(frozen=True, slots=True)
class PromotionReport:
    total_ideas: int
    stayed: int
    promoted_to_concept: int
    promoted_to_project: int
    merged_into_entity: int
    skipped: int
    moves: list[tuple[str, str]] = field(default_factory=list)
    decisions: list[IdeaDecision] = field(default_factory=list)


_SYSTEM_PROMPT = (
    "You classify idea files from a personal-memory corpus. Each idea lives in "
    "ideas/*.md today. For each idea, choose ONE classification:\n"
    "- stay: raw idea, still unformed. Keep where it is.\n"
    "- promote_to_concept: durable mental model that spans multiple projects, "
    "or a reusable design pattern, or an explicit framework with defined terms.\n"
    "- promote_to_project: the file contains a spec or describes a product "
    "being built (domain/pricing/tech stack/shipped artifacts). Or it is the "
    "main notes for an existing project.\n"
    "- merge_with_entity: the idea is evidence for an entity already in the "
    "entity roster. Cite the canonical_slug.\n\n"
    "Rules:\n"
    "1. Default is stay. Only promote/merge when signal is clear.\n"
    "2. For promotions, target_slug is the kebab-case slug for the new page.\n"
    "3. For merges, target_slug is the matching canonical_slug from the roster.\n"
    "4. rationale: one sentence citing what in the file content justifies the "
    "decision.\n"
    "5. Do NOT invent entities. If in doubt, stay."
)

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source_path": {"type": "string"},
                    "classification": {
                        "type": "string",
                        "enum": [
                            "stay",
                            "promote_to_concept",
                            "promote_to_project",
                            "merge_with_entity",
                        ],
                    },
                    "target_slug": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "rationale": {"type": "string"},
                },
                "required": ["source_path", "classification", "target_slug", "rationale"],
            },
        }
    },
    "required": ["decisions"],
}


class PromotionProgress(Protocol):
    def __call__(self, *, index: int, total: int, label: str) -> None: ...


def promote_ideas(
    corpus_root: Path,
    entities: Sequence[CanonicalEntity],
    *,
    client: OpenRouterClient,
    dry_run: bool = False,
    progress: PromotionProgress | None = None,
) -> PromotionReport:
    """Classify every ``ideas/*.md`` and apply promotions/merges."""
    ideas_root = corpus_root / "ideas"
    idea_files = sorted(ideas_root.rglob("*.md")) if ideas_root.exists() else []

    total = len(idea_files)
    stayed = 0
    to_concept = 0
    to_project = 0
    merged = 0
    skipped = 0
    moves: list[tuple[str, str]] = []
    decisions: list[IdeaDecision] = []

    batches = [idea_files[i : i + _BATCH_SIZE] for i in range(0, total, _BATCH_SIZE)]
    for batch_index, batch in enumerate(batches, start=1):
        if progress is not None:
            progress(
                index=batch_index,
                total=len(batches),
                label=f"batch {batch_index}/{len(batches)}",
            )
        batch_decisions = _classify_batch(
            client,
            corpus_root=corpus_root,
            batch=batch,
            entities=entities,
        )
        if batch_decisions is None:
            skipped += len(batch)
            continue
        for decision in batch_decisions:
            decisions.append(decision)
            source_relative = Path(decision.source_path)
            if decision.classification == "stay":
                stayed += 1
                continue
            target_relative = _target_for(decision, corpus_root=corpus_root)
            if target_relative is None:
                # Invalid decision — keep the idea in place.
                stayed += 1
                continue
            if decision.classification == "merge_with_entity":
                _append_backlink(
                    corpus_root=corpus_root,
                    source_relative=source_relative,
                    entity_slug=decision.target_slug or "",
                    dry_run=dry_run,
                )
                merged += 1
                continue
            _move_idea(
                corpus_root=corpus_root,
                source_relative=source_relative,
                target_relative=target_relative,
                classification=decision.classification,
                dry_run=dry_run,
            )
            if decision.classification == "promote_to_concept":
                to_concept += 1
            else:
                to_project += 1
            moves.append((source_relative.as_posix(), target_relative.as_posix()))

    return PromotionReport(
        total_ideas=total,
        stayed=stayed,
        promoted_to_concept=to_concept,
        promoted_to_project=to_project,
        merged_into_entity=merged,
        skipped=skipped,
        moves=moves,
        decisions=decisions,
    )


def _classify_batch(
    client: OpenRouterClient,
    *,
    corpus_root: Path,
    batch: list[Path],
    entities: Sequence[CanonicalEntity],
) -> list[IdeaDecision] | None:
    prompt = _build_batch_prompt(corpus_root=corpus_root, batch=batch, entities=entities)
    try:
        payload = client.generate_json(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=prompt,
            schema_name="dory_idea_promotion",
            schema=_SCHEMA,
        )
    except OpenRouterProviderError:
        return None
    return _parse_decisions(payload)


def _build_batch_prompt(
    *,
    corpus_root: Path,
    batch: list[Path],
    entities: Sequence[CanonicalEntity],
) -> str:
    lines: list[str] = [
        "Entity roster (canonical_slug — family — one-liner):",
    ]
    for entity in entities:
        lines.append(f"- {entity.slug} — {entity.family} — {entity.one_liner}")
    lines.append("")
    lines.append("Classify each idea below:")
    for path in batch:
        relative = path.relative_to(corpus_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        snippet = text[:_MAX_SNIPPET_CHARS]
        lines.append(f"\n--- {relative} ---")
        lines.append(snippet)
    return "\n".join(lines)


def _parse_decisions(payload: Any) -> list[IdeaDecision]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("decisions")
    if not isinstance(raw, list):
        return []
    decisions: list[IdeaDecision] = []
    for item in raw:
        decision = _coerce_decision(item)
        if decision is not None:
            decisions.append(decision)
    return decisions


def _coerce_decision(item: Any) -> IdeaDecision | None:
    if not isinstance(item, dict):
        return None
    source_path = item.get("source_path")
    classification = item.get("classification")
    target_slug = item.get("target_slug")
    rationale = item.get("rationale")
    if not isinstance(source_path, str) or not source_path.strip():
        return None
    if classification not in {
        "stay",
        "promote_to_concept",
        "promote_to_project",
        "merge_with_entity",
    }:
        return None
    if classification != "stay" and (not isinstance(target_slug, str) or not target_slug.strip()):
        return None
    if not isinstance(rationale, str):
        rationale = ""
    return IdeaDecision(
        source_path=source_path.strip(),
        classification=classification,  # type: ignore[arg-type]
        target_slug=target_slug.strip() if isinstance(target_slug, str) else None,
        rationale=rationale.strip(),
    )


def _target_for(
    decision: IdeaDecision,
    *,
    corpus_root: Path,
) -> Path | None:
    if decision.classification == "stay":
        return None
    slug = decision.target_slug
    if not slug:
        return None
    slug = slug.strip().lower()
    if decision.classification == "promote_to_concept":
        return Path("concepts") / f"{slug}.md"
    if decision.classification == "promote_to_project":
        return Path("projects") / slug / "state.md"
    if decision.classification == "merge_with_entity":
        # Backlink target; returned so we can resolve the entity path later.
        return Path("ideas") / Path(decision.source_path).name
    return None


def _move_idea(
    *,
    corpus_root: Path,
    source_relative: Path,
    target_relative: Path,
    classification: Classification,
    dry_run: bool,
) -> None:
    source_absolute = corpus_root / source_relative
    if not source_absolute.exists():
        return
    target_absolute = resolve_corpus_target(corpus_root, target_relative)
    if target_absolute.exists():
        return
    text = source_absolute.read_text(encoding="utf-8")
    try:
        document = load_markdown_document(text)
        body = document.body
        raw_frontmatter = dict(document.frontmatter)
    except ValueError:
        body = text
        raw_frontmatter = {"title": source_relative.stem.replace("-", " ").title()}

    new_type = "concept" if classification == "promote_to_concept" else "project"
    patch = {
        "type": new_type,
        "canonical": classification == "promote_to_project",
        "promoted_from": source_relative.as_posix(),
        "status": "active",
    }
    merged = merge_frontmatter(raw_frontmatter, patch)
    normalized = normalize_frontmatter(merged, target=target_relative)
    rendered = dump_markdown_document(normalized, body)

    if dry_run:
        return

    target_absolute.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target_absolute, rendered, encoding="utf-8")
    source_absolute.unlink()


def _append_backlink(
    *,
    corpus_root: Path,
    source_relative: Path,
    entity_slug: str,
    dry_run: bool,
) -> None:
    """For merges: add a backlink note to the idea file pointing at the entity."""
    if dry_run or not entity_slug:
        return
    absolute = corpus_root / source_relative
    if not absolute.exists():
        return
    text = absolute.read_text(encoding="utf-8")
    marker = f"\n\n<!-- merged-with: {entity_slug} -->\n"
    if marker in text:
        return
    absolute.write_text(text.rstrip() + marker, encoding="utf-8")


def format_promotion_summary(report: PromotionReport) -> dict[str, Any]:
    return {
        "total_ideas": report.total_ideas,
        "stayed": report.stayed,
        "promoted_to_concept": report.promoted_to_concept,
        "promoted_to_project": report.promoted_to_project,
        "merged_into_entity": report.merged_into_entity,
        "skipped": report.skipped,
        "moves_sample": [f"{src} -> {dst}" for src, dst in report.moves[:10]],
    }
