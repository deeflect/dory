from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dory_core.frontmatter import dump_markdown_document
from dory_core.openclaw_parity import OpenClawParityStore, RecallPromotionCandidate
from dory_core.slug import slugify_path_segment


@dataclass(frozen=True, slots=True)
class RecallPromotionWriter:
    root: Path

    def relative_output_path(self, candidate: RecallPromotionCandidate) -> Path:
        slug = slugify_path_segment(candidate.selected_path.replace("/", "-").replace(".md", "")) or "recall"
        return Path("inbox/distilled") / f"recall-{slug}.md"

    def write(self, candidate: RecallPromotionCandidate) -> Path:
        target_rel = self.relative_output_path(candidate)
        target = self.root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_candidate(candidate), encoding="utf-8")
        return target


@dataclass(frozen=True, slots=True)
class RecallPromotionRunner:
    root: Path
    index_root: Path
    min_events: int = 2
    limit: int = 10

    @classmethod
    def create(
        cls,
        *,
        root: Path,
        index_root: Path,
        min_events: int = 2,
        limit: int = 10,
    ) -> "RecallPromotionRunner":
        return cls(root=Path(root), index_root=Path(index_root), min_events=min_events, limit=limit)

    @property
    def writer(self) -> RecallPromotionWriter:
        return RecallPromotionWriter(self.root)

    def collect_candidates(self) -> tuple[RecallPromotionCandidate, ...]:
        store = OpenClawParityStore(self.index_root)
        return store.list_recall_promotion_candidates(min_events=self.min_events, limit=self.limit)

    def materialize(self) -> tuple[str, ...]:
        store = OpenClawParityStore(self.index_root)
        written: list[str] = []
        for candidate in self.collect_candidates():
            target_rel = self.writer.relative_output_path(candidate)
            target = self.root / target_rel
            proposal_target = self.root / "inbox" / "proposed" / f"{target.stem}.json"
            if target.exists() or proposal_target.exists():
                continue
            self.writer.write(candidate)
            store.mark_recall_promotion(candidate=candidate, distilled_path=target_rel.as_posix())
            written.append(target_rel.as_posix())
        return tuple(written)

    def run(self) -> tuple[str, ...]:
        return self.materialize()


def _render_candidate(candidate: RecallPromotionCandidate) -> str:
    title = _title_from_selected_path(candidate.selected_path)
    frontmatter = {
        "title": f"Recall Promotion {title}",
        "type": "note",
        "status": "review",
        "canonical": False,
        "source_kind": "recall-promotion",
        "confidence": "medium",
        "agent_ids": ["openclaw"],
    }
    sections = [
        "## Summary",
        (
            f"`{candidate.selected_path}` was recalled {candidate.event_count} times "
            f"across {candidate.query_count} distinct queries. Review whether this should "
            "be promoted into stronger durable memory."
        ),
        "",
        "## Recall Stats",
        f"- selected_path: `{candidate.selected_path}`",
        f"- recall_count: {candidate.event_count}",
        f"- unique_query_count: {candidate.query_count}",
        f"- latest_at: {candidate.latest_at}",
        "",
        "## Query Samples",
        *(f"- {query}" for query in candidate.sample_queries),
        "",
        "## Suggested Review",
        "- Check whether the recalled material should become a canonical decision, project state update, or concept page update.",
        "- Prefer promotion only when the recalled material is still current and supported by evidence.",
    ]
    return dump_markdown_document(frontmatter, "\n".join(sections).rstrip() + "\n")


def _title_from_selected_path(selected_path: str) -> str:
    stem = Path(selected_path).stem
    if stem == "state":
        stem = Path(selected_path).parent.name
    return stem.replace("-", " ").replace("_", " ").strip().title()
