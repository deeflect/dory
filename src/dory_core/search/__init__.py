"""Search engine: FTS, vector, hybrid, and session-plane search.

This package is the refactored split of ``dory_core/search.py``.
All public symbols from the original module are re-exported here for
backward compatibility.
"""

# Public API — stable exports that external code depends on.
from dory_core.search.engine import SearchEngine
from dory_core.search.fts import _build_fts_query
from dory_core.search.scoring import merge_rankings
from dory_core.search.types import _ChunkRow, QueryProfile
from dory_core.search.utils import _rerank_candidate_from_row
from dory_core.types import SearchMode

# Internal helpers needed by sibling packages (e.g. rerank_orchestrator).
# These are prefixed with _ but are imported externally — treat as semi-public.

__all__ = [
    "SearchEngine",
    "_ChunkRow",
    "QueryProfile",
    "SearchMode",
    "_build_fts_query",
    "merge_rankings",
    "_rerank_candidate_from_row",
]
