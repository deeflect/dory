from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import date as _date_type
from fnmatch import fnmatch
from heapq import nsmallest
from pathlib import Path
from typing import Sequence

from dory_core.embedding import ContentEmbedder, QueryEmbedder
from dory_core.index.sqlite_vector_store import SqliteVectorStore
from dory_core.llm_rerank import LLMReranker
from dory_core.query_expansion import QueryExpander
from dory_core.rerank import resolve_rerank_mode
from dory_core.rerank_orchestrator import RerankOrchestrator
from dory_core.retrieval_planner import SearchQueryPlanner, SearchResultSelector, SearchRetrievalPlan
from dory_core.search.dedup import (
    _collapse_duplicate_documents,
    _is_low_trust_search_document,
    _is_retired_document,
)
from dory_core.search.fts import (
    _build_fts_query,
    _build_query_profile,
    _dedupe_preserve_order,
    _focused_snippet,
)
from dory_core.search.scoring import (
    _HYBRID_CANDIDATE_MULTIPLIER,
    _HYBRID_MIN_CANDIDATES,
    _apply_hybrid_priors,
    _apply_min_relevance_score,
    _confidence_for_row,
    _evidence_class_for_document,
    _fuse_scores,
    _is_live_session_result,
    _merge_result_score,
    _normalized_scores,
    _query_requests_session_evidence,
    _score_lexical_signal,
    _with_rank_scores,
    merge_rankings,
)
from dory_core.search.session import _session_search_query
from dory_core.search.types import _ChunkRow, QueryProfile
from dory_core.search.utils import (
    _build_stale_warning,
    _cosine_similarity,
    _escape_sql_like,
    _extract_document_date,
    _load_frontmatter,
    _searchable_body,
)
from dory_core.session_plane import SessionEvidencePlane
from dory_core.types import SearchReq, SearchResp, SearchResult, SearchScope

_logger = logging.getLogger(__name__)


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


def _selector_candidate_from_result(result: SearchResult) -> dict[str, object]:
    metadata = _selector_metadata(result.frontmatter)
    candidate: dict[str, object] = {
        "path": result.path,
        "snippet": result.snippet,
        "evidence_class": result.evidence_class,
    }
    if result.confidence is not None:
        candidate["confidence"] = result.confidence
    if result.stale_warning:
        candidate["stale"] = True
    if metadata:
        candidate["metadata"] = metadata
    return candidate


def _selector_metadata(frontmatter: dict[str, object]) -> dict[str, object]:
    allowed_keys = ("title", "type", "status", "canonical", "source_kind", "updated", "date")
    metadata: dict[str, object] = {}
    for key in allowed_keys:
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            metadata[key] = value
        elif isinstance(value, bool):
            metadata[key] = value
    return metadata


def _search_row_limit(req: SearchReq) -> int:
    if _scope_has_filters(req.scope):
        return max(req.k * 6, 50)
    return max(req.k * 4, 20)


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


def _parse_scope_date(raw: str) -> _date_type | None:
    candidate = raw.strip()[:10]
    if not candidate:
        return None
    try:
        return _date_type.fromisoformat(candidate)
    except ValueError:
        return None


# Re-export public names at module level for __init__.py convenience


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
        self.rerank_orchestrator = RerankOrchestrator(reranker, rerank_candidate_limit)
        self.db_path = self.index_root / "dory.db"
        self.vector_store = SqliteVectorStore(
            self.index_root / "dory.db",
            dimension=embedder.dimension,
        )
        self.vector_store.import_legacy_json_if_empty(self.index_root / "lance")
        self.session_plane = SessionEvidencePlane(self.index_root / "session_plane.db")

    def search(self, req: SearchReq) -> SearchResp:
        started = time.perf_counter()
        rerank_decision = resolve_rerank_mode(req.rerank, phase=self.rerank_phase)
        warnings: list[str] = []
        search_plan = (
            self._plan_search(req, warnings=warnings)
            if req.mode == "hybrid" and req.corpus != "sessions"
            else None
        )

        if req.mode == "recall" or req.corpus == "sessions":
            response = self._search_session_plane(req.query, req.k, scope=req.scope, started=started)
            return self._finalize_search_response(response, req=req, warnings=warnings)

        durable = self._search_durable(
            req,
            started=started,
            rerank_enabled=rerank_decision.enabled,
            search_plan=search_plan,
            warnings=warnings,
        )
        if req.corpus == "durable":
            return self._finalize_search_response(durable, req=req, warnings=warnings)

        session_queries = search_plan.session_queries if search_plan is not None else (req.query,)
        session = self._search_session_plane_multi(session_queries, req.k, scope=req.scope, started=started)
        merged = self._merge_with_session_results(
            durable,
            session,
            req.query,
            req.k,
            started=started,
            include_session_tail=True,
        )
        return self._finalize_search_response(merged, req=req, warnings=warnings)

    def _finalize_search_response(
        self,
        response: SearchResp,
        *,
        req: SearchReq,
        warnings: list[str],
    ) -> SearchResp:
        response = _apply_min_relevance_score(response, req.min_relevance_score)
        response = self._select_results(response, req=req, warnings=warnings)
        self._record_recall(req.query, response.results)
        return response

    def _search_durable(
        self,
        req: SearchReq,
        *,
        started: float,
        rerank_enabled: bool,
        search_plan: SearchRetrievalPlan | None = None,
        warnings: list[str],
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
            rows = self._hybrid(req.query, row_limit, search_plan=search_plan, warnings=warnings)
        else:  # pragma: no cover - guarded by SearchReq
            raise ValueError(f"unsupported search mode: {mode}")

        if rerank_enabled:
            if self.rerank_orchestrator.reranker is None:
                if req.rerank == "true":
                    warnings.append("Rerank requested but no reranker backend is configured.")
            else:
                rows = self.rerank_orchestrator.rerank(rows, query=req.query, warnings=warnings)

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
            warnings=list(warnings),
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
        warnings: list[str],
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

        expansion_queries = self._expanded_queries(query, warnings=warnings)
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

    def _plan_search(self, req: SearchReq, *, warnings: list[str]) -> SearchRetrievalPlan | None:
        if self.retrieval_planner is None:
            return None
        try:
            return self.retrieval_planner.plan_search(query=req.query, corpus=req.corpus)
        except Exception:
            _logger.exception("retrieval planner failed; falling back to deterministic query planning")
            warnings.append("Retrieval planning failed; search used deterministic query planning.")
            return None

    def _select_results(
        self,
        response: SearchResp,
        *,
        req: SearchReq,
        warnings: list[str],
    ) -> SearchResp:
        if self.result_selector is None or len(response.results) < 2:
            return response
        candidates = tuple(
            _selector_candidate_from_result(result)
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
            warnings.append("Result selection failed; search kept deterministic ranking.")
            return SearchResp(
                query=response.query,
                count=len(response.results),
                results=response.results,
                took_ms=response.took_ms,
                warnings=_dedupe_preserve_order([*response.warnings, *warnings]),
            )
        selected = _with_rank_scores(_reorder_results(response.results, selection.selected_paths))
        return SearchResp(
            query=response.query,
            count=len(selected),
            results=selected,
            took_ms=response.took_ms,
            warnings=_dedupe_preserve_order([*response.warnings, *warnings]),
        )

    def _expanded_queries(self, query: str, *, warnings: list[str]) -> list[str]:
        if self.query_expander is None:
            return [query]
        try:
            expanded = self.query_expander.expand(query)
        except Exception:
            _logger.exception("query expander failed; using base query only")
            warnings.append("Query expansion failed; search used the base query only.")
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
        scope: SearchScope | None = None,
        started: float,
    ) -> SearchResp:
        response = self.session_plane.search(_session_search_query(query=query, limit=limit, scope=scope))
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
        scope: SearchScope | None = None,
        started: float,
    ) -> SearchResp:
        normalized_queries = [query for query in queries if query.strip()]
        if not normalized_queries:
            return self._search_session_plane("", limit, scope=scope, started=started)
        scored_results: dict[str, tuple[float, SearchResult]] = {}
        for query_index, query in enumerate(normalized_queries):
            response = self._search_session_plane(query, limit, scope=scope, started=started)
            for result_index, result in enumerate(response.results, start=1):
                score = float(result.score) - (query_index * 0.1) - (result_index * 0.01)
                existing = scored_results.get(result.path)
                if existing is None or score > existing[0]:
                    scored_results[result.path] = (score, result)
        ordered = sorted(scored_results.values(), key=lambda item: (-item[0], item[1].path))
        merged = _with_rank_scores([result for _score, result in ordered[:limit]])
        took_ms = max(1, int((time.perf_counter() - started) * 1000))
        return SearchResp(query=normalized_queries[0], count=len(merged), results=merged, took_ms=took_ms)

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


# Re-export public names at module level for __init__.py convenience
# These are intentionally aliased from the sibling imports above.
