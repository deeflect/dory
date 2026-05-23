"""
DEPRECATED: Hardcoded search priors, heuristic token sets, and path-based
scoring policies.

These are deterministic keyword/path heuristics that should eventually be
replaced by the retrieval planner (``SearchQueryPlanner`` from
:mod:`dory_core.retrieval_planner``).  New code should use
``SearchQueryPlanner`` instead of relying on these hardcoded constants and
functions.

They are kept here (instead of inlined in :mod:`dory_core.search.scoring`)
so they can be removed as a single unit once the retrieval planner handles
all the cases these heuristics were written for.
"""

from __future__ import annotations


from dory_core.search.dedup import _is_low_trust_search_document
from dory_core.search.types import QueryProfile, _ChunkRow
from dory_core.search.utils import _extract_document_date

# ---------------------------------------------------------------------------
# Heuristic query-token sets
# ---------------------------------------------------------------------------
# TODO(dory-retrieval-planner): Replace all of these with retrieval-planner
# query classification once SearchQueryPlanner handles corpus routing.

_CURRENT_QUERY_TOKENS: frozenset[str] = frozenset(
    {
        "active",
        "current",
        "focus",
        "priorities",
        "priority",
        "today",
        "working",
        "work",
    }
)

_ENV_QUERY_TOKENS: frozenset[str] = frozenset(
    {
        "deploy",
        "deployment",
        "dns",
        "docker",
        "dory",
        "host",
        "homelab",
        "https",
        "network",
        "server",
        "url",
    }
)

_PRIVACY_QUERY_TOKENS: frozenset[str] = frozenset(
    {
        "boundaries",
        "boundary",
        "private",
        "privacy",
        "public",
        "sensitive",
    }
)

_TEMPORAL_QUERY_TOKENS: frozenset[str] = frozenset(
    {
        "date",
        "did",
        "happen",
        "history",
        "historical",
        "last",
        "previous",
        "timeline",
        "when",
        "yesterday",
    }
)

_SESSION_QUERY_TOKENS: frozenset[str] = frozenset(
    {
        "chat",
        "conversation",
        "log",
        "logs",
        "recent",
        "session",
        "sessions",
        "transcript",
        "transcripts",
    }
)

_DIGEST_QUERY_TOKENS: frozenset[str] = frozenset(
    {
        "daily",
        "digest",
        "digests",
        "weekly",
        "week",
    }
)


# ---------------------------------------------------------------------------
# Path-based document priors (document-level boost / penalty)
# ---------------------------------------------------------------------------
# TODO(dory-retrieval-planner): Replace with retrieval planner path policies.


def _score_document_prior(
    row: _ChunkRow,
    frontmatter: dict[str, object],
    query_profile: QueryProfile,
) -> float:
    """Apply a deterministic boost/penalty based on document path and metadata.

    This is a hardcoded heuristic that should eventually be driven by the
    retrieval planner's path/type policies.
    """
    tokens = set(query_profile.tokens)
    path = row.path
    score = 0.0

    if frontmatter.get("canonical") is True:
        score += 0.018
    if str(frontmatter.get("source_kind", "")).strip().lower() == "canonical":
        score += 0.014
    if str(frontmatter.get("status", "")).strip().lower() == "active":
        score += 0.003

    if tokens & _CURRENT_QUERY_TOKENS:
        if path == "core/active.md":
            score += 0.04
        elif path.startswith("projects/") and path.endswith("/state.md"):
            score += 0.012

    if tokens & _ENV_QUERY_TOKENS:
        if path == "core/env.md":
            score += 0.04
        elif path == "projects/dory/state.md":
            score += 0.018

    if tokens & _PRIVACY_QUERY_TOKENS:
        if path in {"core/user.md", "core/identity.md", "core/defaults.md", "core/soul.md"}:
            score += 0.035
        elif path.startswith("knowledge/personal/"):
            score -= 0.02
    visibility = str(frontmatter.get("visibility", "")).strip().lower()
    sensitivity = str(frontmatter.get("sensitivity", "")).strip().lower()
    if visibility == "private" and not (tokens & _PRIVACY_QUERY_TOKENS):
        score -= 0.025
    if sensitivity and sensitivity != "none" and not (tokens & _PRIVACY_QUERY_TOKENS):
        score -= 0.018

    source_kind = str(frontmatter.get("source_kind", "")).strip().lower()
    status = str(frontmatter.get("status", "")).strip().lower()
    temporal_query = query_profile.has_temporal_hint
    digest_query = bool(tokens & _DIGEST_QUERY_TOKENS)
    if temporal_query and (path.startswith("logs/daily/") or _extract_document_date(frontmatter) is not None):
        score += 0.09 if path.startswith("logs/daily/") else 0.045
    if digest_query:
        if path.startswith("digests/"):
            score += 0.2
        elif path.startswith(("logs/daily/", "logs/weekly/")):
            score += 0.06
    if path.startswith("inbox/"):
        score -= 0.04
    if path.startswith("logs/") and not temporal_query:
        score -= 0.03
    if status == "raw":
        score -= 0.03
    if source_kind == "generated" and not (digest_query and path.startswith("digests/")):
        score -= 0.018
    if _is_low_trust_search_document(path, frontmatter):
        score -= 0.05

    return score


# ---------------------------------------------------------------------------
# Source / evidence-class merge priors
# ---------------------------------------------------------------------------
# TODO(dory-retrieval-planner): Replace with retrieval-planner source policies.


def _query_requests_session_evidence(query_profile: QueryProfile) -> bool:
    """Check whether the query profile hints at session evidence.

    Returns ``True`` when the query has a temporal hint or the token set
    overlaps ``_SESSION_QUERY_TOKENS`` or ``_PRIVACY_QUERY_TOKENS``.
    """
    tokens = set(query_profile.tokens)
    return query_profile.has_temporal_hint or bool(tokens & (_SESSION_QUERY_TOKENS | _PRIVACY_QUERY_TOKENS))


def _is_live_session_result(result: object) -> bool:
    """Check whether a session result is still live (active / interrupted)."""
    status = str(result.frontmatter.get("status", "")).strip().lower()
    return status in {"active", "interrupted"}


def _merge_source_prior(result: object, *, query_profile: QueryProfile, source: str) -> float:
    """Apply a deterministic boost/penalty when merging result sources.

    This is the merge-phase counterpart of ``_score_document_prior``,
    applied to already-built ``SearchResult`` objects during session/durable
    merge.
    """
    path = result.path
    frontmatter = result.frontmatter
    evidence_class = result.evidence_class
    source_kind = str(frontmatter.get("source_kind", "")).strip().lower()
    is_canonical = frontmatter.get("canonical") is True or source_kind == "canonical" or evidence_class == "canonical"
    wants_sessions = _query_requests_session_evidence(query_profile)
    tokens = set(query_profile.tokens)

    if source == "session" or path.startswith("logs/sessions/") or evidence_class == "session":
        if wants_sessions:
            return 0.015
        return -0.18 if _is_live_session_result(result) else -0.09

    score = 0.0
    if is_canonical:
        score += 0.055
    if path.startswith("core/"):
        score += 0.035
    if path.startswith("projects/") and path.endswith("/state.md"):
        score += 0.03
    wants_digest = bool(tokens & _DIGEST_QUERY_TOKENS)
    if path.startswith("digests/") and wants_digest:
        score += 0.16
    if path.startswith("logs/") and tokens & _DIGEST_QUERY_TOKENS:
        score += 0.025
    if path == "core/active.md" and tokens & _CURRENT_QUERY_TOKENS:
        score += 0.035
    if path == "core/env.md" and tokens & _ENV_QUERY_TOKENS:
        score += 0.035
    if tokens & _PRIVACY_QUERY_TOKENS:
        if path in {"core/user.md", "core/identity.md", "core/defaults.md", "core/soul.md"}:
            score += 0.06
        if path.startswith(("knowledge/personal", "knowledge/personal-db")):
            score -= 0.06
    visibility = str(frontmatter.get("visibility", "")).strip().lower()
    sensitivity = str(frontmatter.get("sensitivity", "")).strip().lower()
    if visibility == "private" and not (tokens & _PRIVACY_QUERY_TOKENS):
        score -= 0.035
    if sensitivity and sensitivity != "none" and not (tokens & _PRIVACY_QUERY_TOKENS):
        score -= 0.025
    if (evidence_class == "generated" and not (path.startswith("digests/") and wants_digest)) or path.startswith("wiki/"):
        score -= 0.03
    if evidence_class in {"inbox", "raw", "archive"}:
        score -= 0.045
    return score
