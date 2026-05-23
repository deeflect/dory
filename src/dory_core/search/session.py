from __future__ import annotations

from dory_core.search.fts import _dedupe_preserve_order
from dory_core.search.utils import _normalized_filter_values
from dory_core.session_plane import SessionSearchQuery
from dory_core.types import SearchScope


def _session_search_query(*, query: str, limit: int, scope: SearchScope | None) -> SessionSearchQuery:
    if scope is None:
        return SessionSearchQuery(query=query, limit=limit)
    session_ids = tuple(_normalized_filter_values(scope.session_id))
    if scope.session_key is not None and scope.session_key.strip():
        session_ids = _dedupe_preserve_order([*session_ids, scope.session_key.strip()])
    return SessionSearchQuery(
        query=query,
        limit=limit,
        agents=tuple(_normalized_filter_values(scope.agent)),
        devices=tuple(_normalized_filter_values(scope.device)),
        session_ids=session_ids,
        statuses=tuple(_normalized_filter_values(scope.status)),
        since=scope.since,
        until=scope.until,
    )

