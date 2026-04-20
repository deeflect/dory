"""LLM-assisted routing for review-case source files.

The deterministic source router (``migration_source_router``) routes
roughly 95% of the live corpus cleanly. The remaining cases — typically
ambiguous root-level dated files and unlabelled "supporting" folders —
get flagged with ``kind="review"``. This module upgrades a review
decision to a routed one by asking the LLM to pick a destination bucket
based on the file's content.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from dory_core.llm.openrouter import OpenRouterClient, OpenRouterProviderError
from dory_core.migration_source_router import RoutingDecision
from dory_core.slug import slugify_path_segment


_BUCKET_CHOICES = (
    "logs/daily",
    "logs/weekly",
    "logs/sessions",
    "digests/daily",
    "digests/weekly",
    "projects",
    "ideas",
    "decisions",
    "people",
    "concepts",
    "knowledge",
    "references/notes",
    "references/reports",
    "references/briefings",
    "references/tweets",
    "inbox",
    "archive",
)

_SYSTEM_PROMPT = (
    "You route ambiguous legacy-memory files into one of a fixed set of "
    "canonical buckets. Pick the single best bucket for the file given "
    "its content. If no bucket clearly fits, pick 'inbox'. Return only "
    "the chosen bucket and a one-sentence rationale."
)

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "bucket": {"type": "string", "enum": list(_BUCKET_CHOICES)},
        "filename_hint": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "reason": {"type": "string"},
    },
    "required": ["bucket", "filename_hint", "reason"],
}

_MAX_CONTENT_CHARS = 4000


class ReviewRouter(Protocol):
    def resolve(self, decision: RoutingDecision) -> RoutingDecision: ...


@dataclass(frozen=True, slots=True)
class OpenRouterReviewRouter:
    client: OpenRouterClient

    def resolve(self, decision: RoutingDecision) -> RoutingDecision:
        if decision.kind != "review":
            return decision
        try:
            content = decision.source_path.read_text(encoding="utf-8")
        except OSError:
            return decision

        prompt = _build_user_prompt(decision, content=content)
        try:
            payload = self.client.generate_json(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=prompt,
                schema_name="dory_review_routing",
                schema=_SCHEMA,
            )
        except OpenRouterProviderError:
            return decision

        return _upgrade_decision(decision, payload)


def _build_user_prompt(decision: RoutingDecision, *, content: str) -> str:
    truncated = content[:_MAX_CONTENT_CHARS]
    bucket_list = "\n".join(f"- {bucket}" for bucket in _BUCKET_CHOICES)
    return (
        f"File: {decision.source_path.name}\n"
        f"Original source path: {decision.source_path}\n"
        f"Review reason: {decision.reason}\n\n"
        f"Allowed buckets:\n{bucket_list}\n\n"
        "File content (may be truncated):\n\n"
        f"{truncated}"
    )


def _upgrade_decision(decision: RoutingDecision, payload: Any) -> RoutingDecision:
    if not isinstance(payload, dict):
        return decision
    bucket = payload.get("bucket")
    reason = payload.get("reason")
    filename_hint = payload.get("filename_hint")
    if bucket not in _BUCKET_CHOICES or not isinstance(reason, str):
        return decision

    destination = _compose_destination(
        bucket=bucket,
        source_path=decision.source_path,
        filename_hint=filename_hint if isinstance(filename_hint, str) else None,
    )
    return RoutingDecision.route(
        decision.source_path,
        destination,
        reason=f"llm-routed: {reason.strip()}",
        tags=(*decision.tags, "llm-routed"),
    )


def _compose_destination(
    *,
    bucket: str,
    source_path: Path,
    filename_hint: str | None,
) -> Path:
    stem = source_path.stem
    hint_slug = slugify_path_segment(filename_hint) if filename_hint else ""
    slug = hint_slug or slugify_path_segment(stem) or "untitled"

    if bucket == "projects":
        return Path(bucket) / slug / "state.md"
    if bucket in {"logs/daily", "digests/daily"}:
        date = stem[:10] if len(stem) >= 10 and stem[:10].count("-") == 2 else slug
        return Path(bucket) / f"{date}.md"
    if bucket in {"logs/weekly", "digests/weekly"}:
        return Path(bucket) / f"{slug}.md"
    if bucket == "logs/sessions":
        return Path(bucket) / f"{slug}.md"
    return Path(bucket) / f"{slug}.md"
