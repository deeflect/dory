from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dory_core.llm.openrouter import OpenRouterClient


class QueryExpander(Protocol):
    def expand(self, query: str) -> list[str]: ...


_QUERY_EXPANSION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "expansions": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["expansions"],
}


@dataclass(frozen=True, slots=True)
class OpenRouterQueryExpander:
    client: OpenRouterClient
    max_expansions: int = 2

    def expand(self, query: str) -> list[str]:
        if self.max_expansions <= 0 or not query.strip():
            return []
        payload = self.client.generate_json(
            system_prompt=(
                "You rewrite personal knowledge-base queries into short alternate searches. "
                "Preserve intent. Favor aliases, renames, punctuated identifiers, and exact tool names. "
                "Do not invent facts. Return only compact search queries."
            ),
            user_prompt=(
                f"Original query:\n{query}\n\n"
                f"Return up to {self.max_expansions} alternate search queries that improve recall."
            ),
            schema_name="query_expansions",
            schema=_QUERY_EXPANSION_SCHEMA,
        )
        raw_expansions = _coerce_expansion_items(payload)
        if not raw_expansions:
            return []

        normalized: list[str] = []
        seen = {query.strip().lower()}
        for item in raw_expansions:
            if not isinstance(item, str):
                continue
            candidate = " ".join(item.split())
            if not candidate:
                continue
            key = candidate.lower()
            if key in seen:
                continue
            normalized.append(candidate)
            seen.add(key)
            if len(normalized) >= self.max_expansions:
                break
        return normalized


def _coerce_expansion_items(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    expansions = payload.get("expansions")
    if not isinstance(expansions, list):
        return []
    return [item for item in expansions if isinstance(item, str)]
