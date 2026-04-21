from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from difflib import SequenceMatcher
from fnmatch import fnmatch
from heapq import nsmallest
from pathlib import Path
from typing import Sequence

from dory_core.embedding import ContentEmbedder, QueryEmbedder
from dory_core.frontmatter import load_markdown_document
from dory_core.index.sqlite_vector_store import SqliteVectorStore
from dory_core.llm_rerank import LLMReranker, RerankCandidate
from dory_core.query_expansion import QueryExpander
from dory_core.retrieval_planner import SearchQueryPlanner, SearchResultSelector, SearchRetrievalPlan
from dory_core.rerank import resolve_rerank_mode
from dory_core.schema import TIMELINE_MARKER
from dory_core.session_plane import SessionEvidencePlane, SessionSearchQuery
from dory_core.types import SearchMode, SearchReq, SearchResp, SearchResult, SearchScope

_logger = logging.getLogger(__name__)
_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_FTS_SEGMENT_RE = re.compile(r"[A-Za-z0-9]+(?:[./@_-][A-Za-z0-9]+)+")
_FTS_QUOTED_SEGMENT_RE = re.compile(r"`([^`]+)`")
_HYBRID_MIN_CANDIDATES = 20
_HYBRID_CANDIDATE_MULTIPLIER = 4
_STOPWORD_TOKENS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "did",
    "do",
    "for",
    "how",
    "i",
    "in",
    "is",
    "it",
    "meant",
    "of",
    "on",
    "other",
    "or",
    "pick",
    "run",
    "stop",
    "the",
    "to",
    "using",
    "we",
    "what",
    "when",
    "why",
}
_TIMELINE_ENTRY_RE = re.compile(r"(?m)^\s*-\s*(\d{4}-\d{2}-\d{2}):")
_STALE_GRACE_DAYS = 7
_CURRENT_QUERY_TOKENS = {"active", "current", "focus", "priorities", "priority", "today", "working", "work"}
_ENV_QUERY_TOKENS = {
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
_PRIVACY_QUERY_TOKENS = {
    "boundaries",
    "boundary",
    "private",
    "privacy",
    "public",
    "sensitive",
}
_TEMPORAL_QUERY_TOKENS = {
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
_SESSION_QUERY_TOKENS = {
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


@dataclass(frozen=True, slots=True)
class QueryProfile:
    tokens: tuple[str, ...]
    phrases: tuple[str, ...]
    has_identifier_hint: bool
    has_temporal_hint: bool


def _build_fts_query(query: str) -> str:
    """Turn a free-form user query into an FTS5-safe expression.

    FTS5 treats punctuation like ``.``, ``-``, ``/`` as syntax when it shows up
    bare. Terms such as ``GPT-5.4`` or ``foo.bar`` blow up the parser. We
    preserve meaningful punctuated identifiers as phrases, drop low-signal
    stopwords, and OR the remaining clauses together so BM25 scores the actual
    nouns and tool names instead of generic question glue.
    """
    clauses: list[str] = []
    seen: set[str] = set()

    for segment in _FTS_QUOTED_SEGMENT_RE.findall(query):
        parts = [part.lower() for part in _FTS_TOKEN_RE.findall(segment)]
        if len(parts) < 2:
            continue
        phrase = " ".join(parts)
        if phrase and phrase not in seen:
            clauses.append(f'"{phrase}"')
            seen.add(phrase)
        for part in parts:
            if part in _STOPWORD_TOKENS or len(part) < 2 or part in seen:
                continue
            clauses.append(f'"{part}"')
            seen.add(part)

    for segment in _FTS_SEGMENT_RE.findall(query):
        parts = [part.lower() for part in _FTS_TOKEN_RE.findall(segment)]
        if len(parts) < 2:
            continue
        phrase = " ".join(parts)
        if phrase and phrase not in seen:
            clauses.append(f'"{phrase}"')
            seen.add(phrase)
        for part in parts:
            if part in _STOPWORD_TOKENS or len(part) < 2 or part in seen:
                continue
            clauses.append(f'"{part}"')
            seen.add(part)

    for token in _FTS_TOKEN_RE.findall(query):
        lowered = token.lower()
        if lowered in _STOPWORD_TOKENS or len(lowered) < 2 or lowered in seen:
            continue
        clauses.append(f'"{lowered}"')
        seen.add(lowered)

    if not clauses:
        return ""
    return " OR ".join(clauses)


def _build_query_profile(query: str) -> QueryProfile:
    raw_tokens = [token.lower() for token in _FTS_TOKEN_RE.findall(query)]
    normalized_tokens = _dedupe_preserve_order(
        _normalize_match_token(token) for token in raw_tokens if token not in _STOPWORD_TOKENS and len(token) >= 3
    )
    phrases = _dedupe_preserve_order(_extract_match_phrases(query))
    has_identifier_hint = bool(_FTS_SEGMENT_RE.search(query)) or any(char in query for char in "@/._-")
    has_temporal_hint = bool(set(raw_tokens) & _TEMPORAL_QUERY_TOKENS)
    return QueryProfile(
        tokens=normalized_tokens,
        phrases=phrases,
        has_identifier_hint=has_identifier_hint,
        has_temporal_hint=has_temporal_hint,
    )


@dataclass(frozen=True, slots=True)
class _ChunkRow:
    chunk_id: str
    path: str
    content: str
    start_line: int
    end_line: int
    frontmatter_json: str
    score: float


def merge_rankings(
    rankings: Sequence[Sequence[str]],
    *,
    limit: int = 10,
    fusion_k: int = 60,
) -> list[str]:
    scores = _fuse_scores(rankings, fusion_k=fusion_k)
    ordered_ids = sorted(scores, key=lambda item: (-scores[item], item))
    return ordered_ids[:limit]


class SearchEngine:
    def __init__(
        self,
        index_root: Path,
        embedder: ContentEmbedder,
        *,
        rerank_phase: str = "v1",
        query_expander: QueryExpander | None = None,
        retrieval_planner: SearchQueryPlanner | None = None,
        result_selector: SearchResultSelector | None = None,
        reranker: LLMReranker | None = None,
        rerank_candidate_limit: int = 40,
    ) -> None:
        self.index_root = Path(index_root)
        self.embedder = embedder
        self.rerank_phase = rerank_phase
        self.query_expander = query_expander
        self.retrieval_planner = retrieval_planner
        self.result_selector = result_selector
        self.reranker = reranker
        self.rerank_candidate_limit = max(2, rerank_candidate_limit)
        self.db_path = self.index_root / "dory.db"
        self.vector_store = SqliteVectorStore(
            self.index_root / "dory.db",
            dimension=embedder.dimension,
        )
        self.vector_store.import_legacy_json_if_empty(self.index_root / "lance")
        self.session_plane = SessionEvidencePlane(self.index_root / "session_plane.db")
        self._warnings: list[str] = []

    def search(self, req: SearchReq) -> SearchResp:
        started = time.perf_counter()
        mode = req.mode
        rerank_decision = resolve_rerank_mode(req.rerank, phase=self.rerank_phase)
        self._warnings = []
        search_plan = self._plan_search(req) if mode == "hybrid" and req.corpus != "sessions" else None

        if mode == "recall" or req.corpus == "sessions":
            response = _apply_min_score(
                self._search_session_plane(req.query, req.k, started=started),
                req.min_score,
            )
            response = self._select_results(response, req=req)
            self._record_recall(req.query, response.results)
            return response
        durable = self._search_durable(
            req,
            started=started,
            rerank_enabled=rerank_decision.enabled,
            search_plan=search_plan,
        )
        if req.corpus == "durable":
            if search_plan is not None and search_plan.include_session_results:
                session = self._search_session_plane_multi(search_plan.session_queries, req.k, started=started)
                merged = _apply_min_score(
                    self._merge_with_session_results(
                        durable,
                        session,
                        req.query,
                        req.k,
                        started=started,
                    ),
                    req.min_score,
                )
                merged = self._select_results(merged, req=req)
                self._record_recall(req.query, merged.results)
                return merged
            if search_plan is None and mode == "hybrid" and self._should_fallback_to_session_plane(req, durable):
                session = self._search_session_plane(req.query, req.k, started=started)
                merged = _apply_min_score(
                    self._merge_with_session_results(durable, session, req.query, req.k, started=started),
                    req.min_score,
                )
                merged = self._select_results(merged, req=req)
                self._record_recall(req.query, merged.results)
                return merged
            durable = _apply_min_score(durable, req.min_score)
            durable = self._select_results(durable, req=req)
            self._record_recall(req.query, durable.results)
            return durable

        session_queries = search_plan.session_queries if search_plan is not None else (req.query,)
        session = self._search_session_plane_multi(session_queries, req.k, started=started)
        if req.corpus == "all":
            merged = _apply_min_score(
                self._merge_with_session_results(
                    durable,
                    session,
                    req.query,
                    req.k,
                    started=started,
                    include_session_tail=True,
                ),
                req.min_score,
            )
            merged = self._select_results(merged, req=req)
            self._record_recall(req.query, merged.results)
            return merged

        if search_plan is None and mode == "hybrid" and self._should_fallback_to_session_plane(req, durable):
            merged = _apply_min_score(
                self._merge_with_session_results(durable, session, req.query, req.k, started=started),
                req.min_score,
            )
            merged = self._select_results(merged, req=req)
            self._record_recall(req.query, merged.results)
            return merged

        durable = _apply_min_score(durable, req.min_score)
        durable = self._select_results(durable, req=req)
        self._record_recall(req.query, durable.results)
        return durable

    def _rerank(self, rows: list[_ChunkRow], query: str) -> list[_ChunkRow]:
        if self.reranker is None or len(rows) < 2:
            return rows

        candidates = [_rerank_candidate_from_row(row) for row in rows]
        try:
            result = self.reranker.rerank(query=query, candidates=candidates)
        except Exception:
            _logger.exception("rerank call failed; falling back to base hybrid ranking")
            self._warnings.append("Rerank failed; kept the base hybrid ranking.")
            return rows
        if result is None:
            self._warnings.append("Rerank returned no usable ranking; kept the base hybrid ranking.")
            return rows

        rows_by_id = {row.chunk_id: row for row in rows}
        reranked: list[_ChunkRow] = []
        for chunk_id in result.ordered_chunk_ids:
            row = rows_by_id.get(chunk_id)
            if row is None:
                continue
            reranked.append(replace(row, score=result.scores.get(chunk_id, row.score)))
        return reranked

    def _rerank_limited(self, rows: list[_ChunkRow], query: str) -> list[_ChunkRow]:
        if len(rows) <= self.rerank_candidate_limit:
            return self._rerank(rows, query)
        self._warnings.append(
            f"Rerank considered the top {self.rerank_candidate_limit} candidates and kept the remaining base order."
        )
        return [
            *self._rerank(rows[: self.rerank_candidate_limit], query),
            *rows[self.rerank_candidate_limit :],
        ]

    def _search_durable(
        self,
        req: SearchReq,
        *,
        started: float,
        rerank_enabled: bool,
        search_plan: SearchRetrievalPlan | None = None,
    ) -> SearchResp:
        mode = req.mode
        row_limit = _search_row_limit(req)
        if mode == "bm25":
            rows = self._bm25(req.query, row_limit)
        elif mode == "exact":
            rows = self._exact(req.query, row_limit)
        elif mode == "vector":
            rows = self._vector(req.query, row_limit)
        elif mode == "hybrid":
            rows = self._hybrid(req.query, row_limit, search_plan=search_plan)
        else:  # pragma: no cover - guarded by SearchReq
            raise ValueError(f"unsupported search mode: {mode}")

        if rerank_enabled:
            if self.reranker is None:
                if req.rerank == "true":
                    self._warnings.append("Rerank requested but no reranker backend is configured.")
            else:
                rows = self._rerank_limited(rows, req.query)

        scope_has_filters = _scope_has_filters(req.scope)
        filtered_rows = []
        seen_paths: set[str] = set()
        for row in _filter_scope_rows(rows, req.scope):
            frontmatter = _load_frontmatter(row.frontmatter_json)
            if _is_retired_document(frontmatter):
                continue
            if not scope_has_filters and _is_low_trust_search_document(row.path, frontmatter):
                continue
            if row.path in seen_paths:
                continue
            seen_paths.add(row.path)
            filtered_rows.append((row, frontmatter))
        if req.mode != "exact":
            filtered_rows = _collapse_duplicate_documents(filtered_rows)
        filtered_rows = filtered_rows[: req.k]

        query_profile = _build_query_profile(req.query)
        normalized_scores = _normalized_scores(
            [row for row, _frontmatter in filtered_rows],
            mode=req.mode,
        )
        results = []
        for position, (row, frontmatter) in enumerate(filtered_rows, start=1):
            rank_score = normalized_scores.get(row.chunk_id)
            results.append(
                SearchResult(
                    path=row.path,
                    lines=f"{row.start_line}-{row.end_line}",
                    score=row.score,
                    score_normalized=rank_score,
                    rank_score=rank_score,
                    evidence_class=_evidence_class_for_document(row.path, frontmatter),
                    snippet=self._make_snippet(row.content, req.include_content, req.query),
                    frontmatter=frontmatter,
                    stale_warning=_build_stale_warning(row.content, frontmatter),
                    confidence=_confidence_for_row(
                        row,
                        frontmatter,
                        query_profile=query_profile,
                        mode=req.mode,
                        position=position,
                    ),
                )
            )

        took_ms = max(1, int((time.perf_counter() - started) * 1000))
        return SearchResp(
            query=req.query,
            count=len(results),
            results=results,
            took_ms=took_ms,
            warnings=list(self._warnings),
        )

    def _bm25(self, query: str, limit: int) -> list[_ChunkRow]:
        fts_query = _build_fts_query(query)
        if not fts_query:
            return []
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    """
                    SELECT
                        c.chunk_id,
                        c.path,
                        c.content,
                        c.start_line,
                        c.end_line,
                        c.frontmatter_json,
                        bm25(chunks_fts) AS score
                    FROM chunks_fts
                    JOIN chunks AS c ON c.chunk_id = chunks_fts.chunk_id
                    WHERE chunks_fts MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                # FTS5 cannot parse this query even after sanitization; fall back empty.
                return []

        return [
            _ChunkRow(
                chunk_id=row["chunk_id"],
                path=row["path"],
                content=row["content"],
                start_line=int(row["start_line"]),
                end_line=int(row["end_line"]),
                frontmatter_json=row["frontmatter_json"],
                score=float(row["score"]),
            )
            for row in rows
        ]

    def _exact(self, query: str, limit: int) -> list[_ChunkRow]:
        needle = query.strip()
        if not needle:
            return []
        pattern = f"%{_escape_sql_like(needle.lower())}%"
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT
                    chunk_id,
                    path,
                    content,
                    start_line,
                    end_line,
                    frontmatter_json
                FROM chunks
                WHERE lower(path) LIKE ? ESCAPE '\\'
                   OR lower(content) LIKE ? ESCAPE '\\'
                   OR lower(frontmatter_json) LIKE ? ESCAPE '\\'
                ORDER BY
                    CASE
                        WHEN lower(path) LIKE ? ESCAPE '\\' THEN 0
                        WHEN lower(frontmatter_json) LIKE ? ESCAPE '\\' THEN 1
                        ELSE 2
                    END,
                    path,
                    start_line
                LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, pattern, limit),
            ).fetchall()

        return [
            _ChunkRow(
                chunk_id=row["chunk_id"],
                path=row["path"],
                content=row["content"],
                start_line=int(row["start_line"]),
                end_line=int(row["end_line"]),
                frontmatter_json=row["frontmatter_json"],
                score=1.0,
            )
            for row in rows
        ]

    def _vector(self, query: str, limit: int) -> list[_ChunkRow]:
        if isinstance(self.embedder, QueryEmbedder):
            query_vector = self.embedder.embed_query(query)
        else:
            query_vector = self.embedder.embed([query])[0]
        scored_rows = (
            (record.chunk_id, _cosine_similarity(query_vector, record.vector))
            for record in self.vector_store.all()
        )
        ranking = nsmallest(
            limit,
            scored_rows,
            key=lambda item: (-item[1], item[0]),
        )
        return self._rows_for_chunk_ids(
            [chunk_id for chunk_id, _score in ranking],
            score_map=dict(ranking),
        )

    def _hybrid(
        self,
        query: str,
        limit: int,
        *,
        search_plan: SearchRetrievalPlan | None = None,
    ) -> list[_ChunkRow]:
        if search_plan is not None and search_plan.durable_queries:
            return self._hybrid_with_queries(search_plan.durable_queries, limit)

        query_profile = _build_query_profile(query)
        candidate_limit = max(_HYBRID_MIN_CANDIDATES, limit * _HYBRID_CANDIDATE_MULTIPLIER)
        base_bm25_rows = self._bm25(query, candidate_limit)
        base_vector_rows = self._vector(query, candidate_limit)
        rows = self._rank_hybrid_rows(
            query_profile=query_profile,
            bm25_rankings=[base_bm25_rows],
            vector_rankings=[base_vector_rows],
            candidate_limit=candidate_limit,
        )
        if not self._should_expand(query_profile, rows):
            return rows[:limit]

        expansion_queries = self._expanded_queries(query)
        if len(expansion_queries) <= 1:
            return rows[:limit]

        bm25_rankings = [base_bm25_rows]
        vector_rankings = [base_vector_rows]
        for expanded_query in expansion_queries[1:]:
            bm25_rankings.append(self._bm25(expanded_query, candidate_limit))
            vector_rankings.append(self._vector(expanded_query, candidate_limit))
        rows = self._rank_hybrid_rows(
            query_profile=query_profile,
            bm25_rankings=bm25_rankings,
            vector_rankings=vector_rankings,
            candidate_limit=candidate_limit,
        )
        return rows[:limit]

    def _hybrid_with_queries(self, queries: Sequence[str], limit: int) -> list[_ChunkRow]:
        base_query = next((query for query in queries if query.strip()), "")
        if not base_query:
            return []
        query_profile = _build_query_profile(base_query)
        candidate_limit = max(_HYBRID_MIN_CANDIDATES, limit * _HYBRID_CANDIDATE_MULTIPLIER)
        bm25_rankings = [self._bm25(query, candidate_limit) for query in queries]
        vector_rankings = [self._vector(query, candidate_limit) for query in queries]
        rows = self._rank_hybrid_rows(
            query_profile=query_profile,
            bm25_rankings=bm25_rankings,
            vector_rankings=vector_rankings,
            candidate_limit=candidate_limit,
        )
        return rows[:limit]

    def _plan_search(self, req: SearchReq) -> SearchRetrievalPlan | None:
        if self.retrieval_planner is None:
            return None
        try:
            return self.retrieval_planner.plan_search(query=req.query, corpus=req.corpus)
        except Exception:
            _logger.exception("retrieval planner failed; falling back to deterministic query planning")
            self._warnings.append("Retrieval planning failed; search used deterministic query planning.")
            return None

    def _select_results(self, response: SearchResp, *, req: SearchReq) -> SearchResp:
        if self.result_selector is None or len(response.results) < 2:
            return response
        candidates = tuple(
            {
                "path": result.path,
                "snippet": result.snippet,
                "score": result.score,
                "rank_score": result.rank_score,
                "evidence_class": result.evidence_class,
                "frontmatter": result.frontmatter,
                "stale_warning": result.stale_warning,
            }
            for result in response.results[: min(len(response.results), 12)]
        )
        try:
            selection = self.result_selector.select_search_results(
                query=req.query,
                corpus=req.corpus,
                candidates=candidates,
            )
        except Exception:
            _logger.exception("result selector failed; keeping deterministic ranking")
            self._warnings.append("Result selection failed; search kept deterministic ranking.")
            return SearchResp(
                query=response.query,
                count=len(response.results),
                results=response.results,
                took_ms=response.took_ms,
                warnings=_dedupe_preserve_order([*response.warnings, *self._warnings]),
            )
        selected = _with_rank_scores(_reorder_results(response.results, selection.selected_paths))
        return SearchResp(
            query=response.query,
            count=len(selected),
            results=selected,
            took_ms=response.took_ms,
            warnings=_dedupe_preserve_order([*response.warnings, *self._warnings]),
        )

    def _expanded_queries(self, query: str) -> list[str]:
        if self.query_expander is None:
            return [query]
        try:
            expanded = self.query_expander.expand(query)
        except Exception:
            _logger.exception("query expander failed; using base query only")
            self._warnings.append("Query expansion failed; search used the base query only.")
            return [query]
        deduped = [query]
        seen = {query.strip().lower()}
        for candidate in expanded:
            key = candidate.strip().lower()
            if not key or key in seen:
                continue
            deduped.append(candidate)
            seen.add(key)
        return deduped

    def _rank_hybrid_rows(
        self,
        *,
        query_profile: QueryProfile,
        bm25_rankings: Sequence[Sequence[_ChunkRow]],
        vector_rankings: Sequence[Sequence[_ChunkRow]],
        candidate_limit: int,
    ) -> list[_ChunkRow]:
        rankings: list[list[str]] = []
        for ranking in bm25_rankings:
            rankings.append([row.chunk_id for row in ranking])
        for ranking in vector_rankings:
            rankings.append([row.chunk_id for row in ranking])
        ranked_ids = merge_rankings(rankings, limit=candidate_limit)
        score_map = _fuse_scores(rankings)
        rows = self._rows_for_chunk_ids(ranked_ids, score_map=score_map)
        return _apply_hybrid_priors(rows, query_profile)

    def _should_expand(
        self,
        query_profile: QueryProfile,
        rows: Sequence[_ChunkRow],
    ) -> bool:
        if self.query_expander is None:
            return False
        if not rows:
            return True

        top_row = rows[0]
        frontmatter = _load_frontmatter(top_row.frontmatter_json)
        lexical_signal = _score_lexical_signal(top_row, frontmatter, query_profile)
        score_margin = top_row.score - rows[1].score if len(rows) > 1 else top_row.score

        strong_result = lexical_signal >= 0.024 or (lexical_signal >= 0.018 and score_margin >= 0.01)
        return not strong_result

    def _rows_for_chunk_ids(
        self,
        chunk_ids: Sequence[str],
        *,
        score_map: dict[str, float] | None = None,
    ) -> list[_ChunkRow]:
        if not chunk_ids:
            return []

        placeholders = ",".join("?" for _ in chunk_ids)
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"""
                SELECT chunk_id, path, content, start_line, end_line, frontmatter_json
                FROM chunks
                WHERE chunk_id IN ({placeholders})
                """,
                list(chunk_ids),
            ).fetchall()

        indexed = {
            row["chunk_id"]: _ChunkRow(
                chunk_id=row["chunk_id"],
                path=row["path"],
                content=row["content"],
                start_line=int(row["start_line"]),
                end_line=int(row["end_line"]),
                frontmatter_json=row["frontmatter_json"],
                score=float((score_map or {}).get(row["chunk_id"], 0.0)),
            )
            for row in rows
        }
        return [indexed[chunk_id] for chunk_id in chunk_ids if chunk_id in indexed]

    @staticmethod
    def _make_snippet(content: str, include_content: bool, query: str = "") -> str:
        body = _searchable_body(content)
        if not body:
            return ""
        focused = _focused_snippet(body, query=query, limit=700 if include_content else 240)
        if focused:
            return focused
        if include_content:
            return body[:700]
        first_line = next(
            (line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")), ""
        )
        return first_line[:240]

    def _search_session_plane(
        self,
        query: str,
        limit: int,
        *,
        started: float,
    ) -> SearchResp:
        response = self.session_plane.search(SessionSearchQuery(query=query, limit=limit))
        results = [
            SearchResult(
                path=result.path,
                lines="1-1",
                score=result.score,
                score_normalized=1.0 if result_index == 1 else max(0.0, 1.0 - ((result_index - 1) * 0.2)),
                rank_score=1.0 if result_index == 1 else max(0.0, 1.0 - ((result_index - 1) * 0.2)),
                evidence_class="session",
                snippet=result.snippet,
                frontmatter={
                    "type": "session",
                    "agent": result.agent,
                    "device": result.device,
                    "session_id": result.session_id,
                    "status": result.status,
                },
                stale_warning="Session evidence: lower trust than canonical memory.",
                confidence="low",
            )
            for result_index, result in enumerate(response.results, start=1)
        ]
        took_ms = max(1, int((time.perf_counter() - started) * 1000))
        return SearchResp(query=query, count=len(results), results=results, took_ms=took_ms)

    def _search_session_plane_multi(
        self,
        queries: Sequence[str],
        limit: int,
        *,
        started: float,
    ) -> SearchResp:
        normalized_queries = [query for query in queries if query.strip()]
        if not normalized_queries:
            return self._search_session_plane("", limit, started=started)
        scored_results: dict[str, tuple[float, SearchResult]] = {}
        for query_index, query in enumerate(normalized_queries):
            response = self._search_session_plane(query, limit, started=started)
            for result_index, result in enumerate(response.results, start=1):
                score = float(result.score) - (query_index * 0.1) - (result_index * 0.01)
                existing = scored_results.get(result.path)
                if existing is None or score > existing[0]:
                    scored_results[result.path] = (score, result)
        ordered = sorted(scored_results.values(), key=lambda item: (-item[0], item[1].path))
        merged = _with_rank_scores([result for _score, result in ordered[:limit]])
        took_ms = max(1, int((time.perf_counter() - started) * 1000))
        return SearchResp(query=normalized_queries[0], count=len(merged), results=merged, took_ms=took_ms)

    def _should_fallback_to_session_plane(self, req: SearchReq, response: SearchResp) -> bool:
        if req.mode != "hybrid":
            return False
        if not response.results:
            return True

        top_result = response.results[0]
        if top_result.path.startswith("logs/sessions/"):
            return False

        query_profile = _build_query_profile(req.query)
        exact_hit_ratio = _score_exact_result_coverage(top_result, query_profile)
        if exact_hit_ratio >= 0.75:
            return False
        return self.session_plane.search(SessionSearchQuery(query=req.query, limit=1)).count > 0

    def _merge_with_session_results(
        self,
        durable: SearchResp,
        session: SearchResp,
        query: str,
        limit: int,
        *,
        started: float,
        include_session_tail: bool = False,
    ) -> SearchResp:
        query_profile = _build_query_profile(query)
        wants_sessions = _query_requests_session_evidence(query_profile)
        scored_results: dict[str, tuple[float, SearchResult]] = {}

        for position, result in enumerate(durable.results, start=1):
            score = _merge_result_score(result, position=position, query_profile=query_profile, source="durable")
            existing = scored_results.get(result.path)
            if existing is None or score > existing[0]:
                scored_results[result.path] = (score, result)

        for position, result in enumerate(session.results, start=1):
            if _is_live_session_result(result) and not wants_sessions:
                continue
            score = _merge_result_score(result, position=position, query_profile=query_profile, source="session")
            existing = scored_results.get(result.path)
            if existing is None or score > existing[0]:
                scored_results[result.path] = (score, result)

        ordered = sorted(scored_results.values(), key=lambda item: (-item[0], item[1].path))
        merged = [result for _score, result in ordered[:limit]]

        if session.results and (wants_sessions or include_session_tail) and not any(
            result.path.startswith("logs/sessions/") for result in merged
        ):
            top_session = next((item for item in ordered if item[1].path.startswith("logs/sessions/")), None)
            if top_session is not None:
                top_session_score, top_session_result = top_session
                if len(merged) < limit:
                    merged.append(top_session_result)
                elif include_session_tail and len(merged) > 1:
                    merged[-1] = top_session_result
                elif wants_sessions and ordered and top_session_score >= ordered[min(limit - 1, len(ordered) - 1)][0] * 0.9:
                    merged[-1] = top_session_result

        took_ms = max(durable.took_ms, session.took_ms)
        return SearchResp(
            query=query,
            count=len(merged),
            results=_with_rank_scores(merged),
            took_ms=took_ms,
            warnings=_dedupe_preserve_order([*durable.warnings, *session.warnings]),
        )

    def _record_recall(self, query: str, results: Sequence[SearchResult]) -> None:
        if not self.db_path.exists():
            return
        payload = json.dumps([result.path for result in results], sort_keys=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO recall_log(query, chunk_ids)
                VALUES (?, ?)
                """,
                (query, payload),
            )
            connection.commit()


def _fuse_scores(
    rankings: Sequence[Sequence[str]],
    *,
    fusion_k: int = 60,
) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for position, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] += 1.0 / (fusion_k + position)
    return dict(scores)


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have the same dimension")

    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _apply_hybrid_priors(
    rows: Sequence[_ChunkRow],
    query_profile: QueryProfile,
) -> list[_ChunkRow]:
    boosted_rows: list[_ChunkRow] = []
    for row in rows:
        frontmatter = _load_frontmatter(row.frontmatter_json)
        lexical_signal = _score_lexical_signal(row, frontmatter, query_profile)
        document_prior = _score_document_prior(row, frontmatter, query_profile)
        boosted_rows.append(replace(row, score=row.score + lexical_signal + document_prior))
    return sorted(boosted_rows, key=lambda row: (-row.score, row.chunk_id))


def _is_retired_document(frontmatter: dict[str, object]) -> bool:
    status = str(frontmatter.get("status", "")).lower()
    if status in {"superseded", "retired"}:
        return True
    superseded_by = frontmatter.get("superseded_by")
    return isinstance(superseded_by, str) and bool(superseded_by.strip())


def _is_low_trust_search_document(path: str, frontmatter: dict[str, object]) -> bool:
    if path.startswith("inbox/quarantine/") or path.endswith(".tombstone.md"):
        return True
    if frontmatter.get("migration_quarantined") is True:
        return True
    status = str(frontmatter.get("status", "")).strip().lower()
    return status in {"quarantined", "quarantine"}


def _collapse_duplicate_documents(
    rows: Sequence[tuple[_ChunkRow, dict[str, object]]],
) -> list[tuple[_ChunkRow, dict[str, object]]]:
    collapsed: list[tuple[_ChunkRow, dict[str, object]]] = []
    for row, frontmatter in rows:
        duplicate_index = next(
            (
                index
                for index, (existing_row, _existing_frontmatter) in enumerate(collapsed)
                if _documents_are_near_duplicates(existing_row, row)
            ),
            None,
        )
        if duplicate_index is None:
            collapsed.append((row, frontmatter))
            continue
        existing_row, existing_frontmatter = collapsed[duplicate_index]
        if _document_precedence(row, frontmatter) > _document_precedence(existing_row, existing_frontmatter):
            collapsed[duplicate_index] = (row, frontmatter)
    return collapsed


def _documents_are_near_duplicates(left: _ChunkRow, right: _ChunkRow) -> bool:
    if left.path == right.path:
        return True
    left_text = _duplicate_signature_text(left.content)
    right_text = _duplicate_signature_text(right.content)
    if len(left_text) < 80 or len(right_text) < 80:
        return False
    shorter, longer = sorted((left_text, right_text), key=len)
    if shorter in longer and len(shorter) / len(longer) >= 0.7:
        return True
    return SequenceMatcher(None, left_text, right_text).ratio() >= 0.9


def _duplicate_signature_text(content: str) -> str:
    body = _searchable_body(content).casefold()
    lines = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return " ".join(" ".join(lines).split())


def _document_precedence(row: _ChunkRow, frontmatter: dict[str, object]) -> tuple[int, float, str]:
    source_kind = str(frontmatter.get("source_kind", "")).strip().lower()
    status = str(frontmatter.get("status", "")).strip().lower()
    score = 0
    if frontmatter.get("canonical") is True:
        score += 100
    if source_kind == "canonical":
        score += 80
    if row.path.startswith("core/"):
        score += 50
    if row.path.startswith("projects/") and row.path.endswith("/state.md"):
        score += 45
    if status == "active":
        score += 10
    if row.path.startswith("wiki/"):
        score -= 40
    if row.path.startswith("sources/semantic/"):
        score -= 35
    if row.path.startswith("inbox/"):
        score -= 25
    if row.path.startswith("logs/"):
        score -= 50
    if source_kind == "generated":
        score -= 20
    return score, row.score, row.path


def _apply_min_score(response: SearchResp, min_score: float) -> SearchResp:
    if min_score == 0.0:
        return response
    results = [result for result in response.results if result.score >= min_score]
    return SearchResp(
        query=response.query,
        count=len(results),
        results=results,
        took_ms=response.took_ms,
        warnings=list(response.warnings),
    )


def _score_lexical_signal(
    row: _ChunkRow,
    frontmatter: dict[str, object],
    query_profile: QueryProfile,
) -> float:
    if not query_profile.tokens and not query_profile.phrases:
        return 0.0

    title = frontmatter.get("title")
    title_text = title if isinstance(title, str) else ""
    content_tokens = set(_extract_normalized_tokens(row.content))
    identifier_tokens = set(_extract_normalized_tokens(" ".join(Path(row.path).parts)))
    identifier_tokens.update(_extract_normalized_tokens(title_text))
    document_tokens = content_tokens | identifier_tokens

    exact_hits = 0
    fuzzy_hits = 0
    for token in query_profile.tokens:
        if token in document_tokens:
            exact_hits += 1
            continue
        if _has_close_identifier_match(token, identifier_tokens):
            fuzzy_hits += 1

    token_count = len(query_profile.tokens)
    coverage_score = 0.0
    if token_count:
        weighted_hits = exact_hits + (fuzzy_hits * 0.55)
        coverage_ratio = weighted_hits / token_count
        coverage_score += min(0.032, coverage_ratio * 0.026)
        if exact_hits == token_count:
            coverage_score += 0.006

    normalized_haystack = _normalize_text_for_matching(f"{title_text}\n{row.content}")
    phrase_matches = sum(1 for phrase in query_profile.phrases if phrase in normalized_haystack)
    if phrase_matches:
        coverage_score += min(0.012, phrase_matches * 0.006)

    return coverage_score


def _score_document_prior(
    row: _ChunkRow,
    frontmatter: dict[str, object],
    query_profile: QueryProfile,
) -> float:
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
    if temporal_query and (path.startswith("logs/daily/") or _extract_document_date(frontmatter) is not None):
        score += 0.09 if path.startswith("logs/daily/") else 0.045
    if path.startswith("inbox/"):
        score -= 0.04
    if path.startswith("logs/") and not temporal_query:
        score -= 0.03
    if status == "raw":
        score -= 0.03
    if source_kind == "generated":
        score -= 0.018
    if _is_low_trust_search_document(path, frontmatter):
        score -= 0.05

    return score


def _score_chunk_exact_coverage(
    row: _ChunkRow,
    frontmatter: dict[str, object],
    query_profile: QueryProfile,
) -> float:
    if not query_profile.tokens:
        return 0.0

    title = frontmatter.get("title")
    title_text = title if isinstance(title, str) else ""
    haystack = " ".join([row.path, title_text, row.content])
    document_tokens = set(_extract_normalized_tokens(haystack))
    exact_hits = sum(1 for token in query_profile.tokens if token in document_tokens)
    return exact_hits / len(query_profile.tokens)


def _confidence_for_row(
    row: _ChunkRow,
    frontmatter: dict[str, object],
    *,
    query_profile: QueryProfile,
    mode: str,
    position: int,
) -> str:
    if mode == "exact":
        return "high"

    coverage = _score_chunk_exact_coverage(row, frontmatter, query_profile)
    if coverage >= 0.75 and position <= 3:
        return "high"
    if coverage >= 0.4 or (position <= 2 and row.score >= 0.04):
        return "medium"
    return "low"


def _score_exact_result_coverage(result: SearchResult, query_profile: QueryProfile) -> float:
    if not query_profile.tokens:
        return 1.0
    haystack = " ".join(
        [
            result.path,
            str(result.frontmatter.get("title", "")),
            result.snippet,
        ]
    )
    matched_tokens = set(_extract_normalized_tokens(haystack))
    exact_hits = sum(1 for token in query_profile.tokens if token in matched_tokens)
    return exact_hits / len(query_profile.tokens)


def _escape_sql_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _extract_document_date(frontmatter: dict[str, object]) -> date | None:
    for key in ("date", "updated", "created"):
        value = frontmatter.get(key)
        if not isinstance(value, str):
            continue
        candidate = value[:10]
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _build_stale_warning(content: str, frontmatter: dict[str, object]) -> str | None:
    if TIMELINE_MARKER not in content:
        return None
    reference_date = _extract_reference_date(frontmatter)
    if reference_date is None:
        return None
    _, _, timeline = content.partition(TIMELINE_MARKER)
    timeline_dates = [date.fromisoformat(match.group(1)) for match in _TIMELINE_ENTRY_RE.finditer(timeline)]
    if not timeline_dates:
        return None
    latest_timeline_date = max(timeline_dates)
    if latest_timeline_date <= reference_date + timedelta(days=_STALE_GRACE_DAYS):
        return None
    return f"compiled truth may be outdated (last timeline entry: {latest_timeline_date.isoformat()})"


def _extract_reference_date(frontmatter: dict[str, object]) -> date | None:
    for key in ("updated", "created", "date"):
        value = frontmatter.get(key)
        if not isinstance(value, str):
            continue
        candidate = value[:10]
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _load_frontmatter(payload: str) -> dict[str, object]:
    if not payload:
        return {}
    return json.loads(payload)


def _searchable_body(content: str) -> str:
    if not content:
        return ""
    try:
        return load_markdown_document(content).body.strip()
    except ValueError:
        return content.strip()


def _focused_snippet(body: str, *, query: str, limit: int) -> str:
    query_profile = _build_query_profile(query)
    if not query_profile.tokens:
        return ""
    query_tokens = set(query_profile.tokens)
    lines = [line.strip() for line in body.splitlines()]
    content_lines = [line for line in lines if line and not line.startswith("#")]
    if not content_lines:
        return ""
    for index, line in enumerate(content_lines):
        line_tokens = set(_extract_normalized_tokens(line))
        if not (query_tokens & line_tokens):
            continue
        start = max(0, index - 1)
        end = min(len(content_lines), index + 3)
        return " ".join(" ".join(content_lines[start:end]).split())[:limit]
    return ""


def _rerank_candidate_from_row(row: _ChunkRow) -> RerankCandidate:
    frontmatter = _load_frontmatter(row.frontmatter_json)
    hints: dict[str, str] = {}
    for key in ("type", "status", "canonical", "source_kind", "temperature", "date", "updated"):
        value = frontmatter.get(key)
        if value is None:
            continue
        hints[key] = str(value)
    title = frontmatter.get("title")
    return RerankCandidate(
        chunk_id=row.chunk_id,
        path=row.path,
        title=str(title) if isinstance(title, str) else "",
        snippet=_searchable_body(row.content),
        frontmatter_hints=hints,
    )


def _merge_result_score(
    result: SearchResult,
    *,
    position: int,
    query_profile: QueryProfile,
    source: str,
) -> float:
    coverage = _score_exact_result_coverage(result, query_profile)
    rank_score = 1.0 / (20 + position)
    source_bonus = _merge_source_prior(result, query_profile=query_profile, source=source)
    return rank_score + (coverage * 0.08) + source_bonus


def _merge_source_prior(result: SearchResult, *, query_profile: QueryProfile, source: str) -> float:
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
    if evidence_class == "generated" or path.startswith("wiki/"):
        score -= 0.03
    if evidence_class in {"inbox", "raw", "archive"}:
        score -= 0.045
    return score


def _query_requests_session_evidence(query_profile: QueryProfile) -> bool:
    tokens = set(query_profile.tokens)
    return query_profile.has_temporal_hint or bool(tokens & (_SESSION_QUERY_TOKENS | _PRIVACY_QUERY_TOKENS))


def _is_live_session_result(result: SearchResult) -> bool:
    status = str(result.frontmatter.get("status", "")).strip().lower()
    return status in {"active", "interrupted"}


def _evidence_class_for_document(path: str, frontmatter: dict[str, object]) -> str:
    if path.startswith("logs/sessions/"):
        return "session"
    if path.startswith("inbox/"):
        return "inbox"
    if path.startswith("archive/"):
        return "archive"
    status = str(frontmatter.get("status", "")).strip().lower()
    if status == "raw":
        return "raw"
    source_kind = str(frontmatter.get("source_kind", "")).strip().lower()
    if source_kind == "generated":
        return "generated"
    if frontmatter.get("canonical") is True or source_kind == "canonical":
        return "canonical"
    return "other"


def _search_row_limit(req: SearchReq) -> int:
    """Pull more candidate rows than k so retired/legacy filtering has headroom.

    Previously returned ``req.k`` when no scope filters were set, which meant
    queries where the top-k candidates were all archived (e.g. "what's the
    default model right now") dropped every result and returned empty.
    """
    if _scope_has_filters(req.scope):
        return max(req.k * 6, 50)
    return max(req.k * 4, 20)


def _normalized_scores(rows: Sequence[_ChunkRow], *, mode: SearchMode) -> dict[str, float]:
    if not rows:
        return {}
    if mode == "exact":
        return {row.chunk_id: 1.0 for row in rows}

    # BM25 scores sort ascending, while vector/hybrid scores sort descending.
    values = [-row.score if mode == "bm25" else row.score for row in rows]
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return {row.chunk_id: _rank_normalized_score(index, total=len(rows)) for index, row in enumerate(rows, start=1)}
    return {
        row.chunk_id: max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))
        for row, value in zip(rows, values, strict=True)
    }


def _rank_normalized_score(position: int, *, total: int) -> float:
    if total <= 1:
        return 1.0
    return max(0.0, 1.0 - ((position - 1) / (total - 1)))


def _scope_has_filters(scope: SearchScope) -> bool:
    return bool(scope.path_glob or scope.type or scope.status or scope.tags or scope.since or scope.until)


def _filter_scope_rows(rows: Sequence[_ChunkRow], scope: SearchScope) -> list[_ChunkRow]:
    if not _scope_has_filters(scope):
        return list(rows)
    return [row for row in rows if _row_matches_scope(row, scope)]


def _row_matches_scope(row: _ChunkRow, scope: SearchScope) -> bool:
    if scope.path_glob and not fnmatch(row.path, scope.path_glob):
        return False

    frontmatter = _load_frontmatter(row.frontmatter_json)
    doc_type = str(frontmatter.get("type", "")).strip().lower()
    if scope.type and doc_type not in {value.strip().lower() for value in scope.type if value.strip()}:
        return False

    status = str(frontmatter.get("status", "")).strip().lower()
    if scope.status and status not in {value.strip().lower() for value in scope.status if value.strip()}:
        return False

    if scope.tags:
        scope_tags = {value.strip().lower() for value in scope.tags if value.strip()}
        doc_tags = _frontmatter_tag_set(frontmatter)
        if not scope_tags.issubset(doc_tags):
            return False

    document_date = _extract_document_date(frontmatter)
    if scope.since is not None:
        since_date = _parse_scope_date(scope.since)
        if since_date is None or document_date is None or document_date < since_date:
            return False
    if scope.until is not None:
        until_date = _parse_scope_date(scope.until)
        if until_date is None or document_date is None or document_date > until_date:
            return False

    return True


def _frontmatter_tag_set(frontmatter: dict[str, object]) -> set[str]:
    raw_tags = frontmatter.get("tags")
    if isinstance(raw_tags, str):
        return {raw_tags.strip().lower()} if raw_tags.strip() else set()
    if isinstance(raw_tags, list):
        return {value.strip().lower() for value in raw_tags if isinstance(value, str) and value.strip()}
    return set()


def _parse_scope_date(raw: str) -> date | None:
    candidate = raw.strip()[:10]
    if not candidate:
        return None
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        return None


def _extract_match_phrases(query: str) -> tuple[str, ...]:
    phrases: list[str] = []
    for segment in _FTS_QUOTED_SEGMENT_RE.findall(query):
        normalized = _normalize_text_for_matching(segment)
        if normalized:
            phrases.append(normalized)
    for segment in _FTS_SEGMENT_RE.findall(query):
        normalized = _normalize_text_for_matching(segment)
        if normalized:
            phrases.append(normalized)
    return tuple(phrases)


def _extract_normalized_tokens(text: str) -> tuple[str, ...]:
    return tuple(_normalize_match_token(token) for token in _FTS_TOKEN_RE.findall(text.lower()) if len(token) >= 2)


def _normalize_text_for_matching(text: str) -> str:
    return " ".join(_extract_normalized_tokens(text))


def _normalize_match_token(token: str) -> str:
    lowered = token.lower()
    if len(lowered) > 5 and lowered.endswith("ing"):
        return lowered[:-3]
    if len(lowered) > 4 and lowered.endswith("ied"):
        return f"{lowered[:-3]}y"
    if len(lowered) > 4 and lowered.endswith("ed"):
        return lowered[:-2]
    if len(lowered) > 4 and lowered.endswith("es"):
        return lowered[:-2]
    if len(lowered) > 3 and lowered.endswith("s"):
        return lowered[:-1]
    return lowered


def _has_close_identifier_match(token: str, identifier_tokens: set[str]) -> bool:
    if len(token) < 5 or not identifier_tokens:
        return False
    for candidate in identifier_tokens:
        if abs(len(token) - len(candidate)) > 2:
            continue
        if token[0] != candidate[0]:
            continue
        if SequenceMatcher(None, token, candidate).ratio() >= 0.82:
            return True
    return False


def _dedupe_preserve_order(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _reorder_results(
    results: Sequence[SearchResult],
    selected_paths: Sequence[str],
) -> list[SearchResult]:
    if not results:
        return []
    if not selected_paths:
        return list(results)
    indexed = {result.path: result for result in results}
    ordered: list[SearchResult] = []
    seen: set[str] = set()
    for path in selected_paths:
        result = indexed.get(path)
        if result is None or path in seen:
            continue
        ordered.append(result)
        seen.add(path)
    for result in results:
        if result.path in seen:
            continue
        ordered.append(result)
    return ordered


def _with_rank_scores(results: Sequence[SearchResult]) -> list[SearchResult]:
    total = len(results)
    return [
        result.model_copy(update={"rank_score": _rank_normalized_score(position, total=total)})
        for position, result in enumerate(results, start=1)
    ]
