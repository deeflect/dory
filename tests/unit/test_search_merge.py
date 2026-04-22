from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from dory_core.search import _ChunkRow, SearchEngine, _build_fts_query, merge_rankings
from dory_core.types import SearchReq, SearchResult


def test_merge_rankings_prefers_shared_hits() -> None:
    merged = merge_rankings([["a", "b"], ["b", "c"]], limit=3)

    assert merged[0] == "b"
    assert set(merged) == {"a", "b", "c"}


def test_build_fts_query_keeps_meaningful_phrases_and_drops_glue_words() -> None:
    query = _build_fts_query("Why did we stop using `qmd query` and what did we switch to?")

    assert '"qmd query"' in query
    assert '"qmd"' in query
    assert '"query"' in query
    assert '"why"' not in query
    assert '"did"' not in query
    assert '"using"' not in query


def test_build_fts_query_preserves_punctuated_identifiers() -> None:
    query = _build_fts_query("Using official X API + @xdevplatform/xdk")

    assert '"xdevplatform xdk"' in query
    assert '"xdevplatform"' in query
    assert '"xdk"' in query


class _FakeEmbedder:
    dimension = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class _FakeQueryExpander:
    def expand(self, query: str) -> list[str]:
        if "Clawzy" in query:
            return ["Clawsy pricing Hetzner"]
        return []


class _ExplodingQueryExpander:
    def expand(self, query: str) -> list[str]:
        raise RuntimeError("broken expander")


class _FakeRetrievalPlanner:
    def plan_search(self, *, query: str, corpus: str):
        del corpus
        from dory_core.retrieval_planner import SearchRetrievalPlan

        if "Clawzy" in query:
            return SearchRetrievalPlan(
                durable_queries=(query, "Clawsy pricing Hetzner"),
                session_queries=(),
                include_session_results=False,
            )
        return SearchRetrievalPlan(
            durable_queries=(query,),
            session_queries=(),
            include_session_results=False,
        )


class _SessionAwarePlanner:
    def plan_search(self, *, query: str, corpus: str):
        del query, corpus
        from dory_core.retrieval_planner import SearchRetrievalPlan

        return SearchRetrievalPlan(
            durable_queries=("rooster focus",),
            session_queries=("rooster follow-up",),
            include_session_results=True,
        )


class _FakeResultSelector:
    def select_search_results(self, *, query: str, corpus: str, candidates):
        del query, corpus, candidates
        from dory_core.retrieval_planner import SearchSelection

        return SearchSelection(
            selected_paths=(
                "projects/clawsy/state.md",
                "projects/claws-studio/state.md",
            )
        )


class _ExplodingResultSelector:
    def select_search_results(self, *, query: str, corpus: str, candidates):
        del query, corpus, candidates
        raise RuntimeError("selector broke")


class _FakeResultSelector:
    def select_search_results(self, *, query: str, corpus: str, candidates):
        del query, corpus, candidates
        from dory_core.retrieval_planner import SearchSelection

        return SearchSelection(
            selected_paths=(
                "projects/clawsy/state.md",
                "projects/claws-studio/state.md",
            )
        )


class _ExplodingResultSelector:
    def select_search_results(self, *, query: str, corpus: str, candidates):
        del query, corpus, candidates
        raise RuntimeError("broken selector")


def test_hybrid_uses_expanded_queries_for_bm25_recall(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder(), query_expander=_FakeQueryExpander())
    rows = {
        "clawsy": _ChunkRow(
            chunk_id="clawsy",
            path="projects/clawsy/state.md",
            content="Pricing: $19/mo BYOK. Hetzner CX22.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"clawsy","type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"warm"}',
            score=0.0,
        ),
        "studio": _ChunkRow(
            chunk_id="studio",
            path="projects/claws-studio/state.md",
            content="Hosted site plans.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"claws studio","type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"warm"}',
            score=0.0,
        ),
    }
    seen_queries: list[str] = []

    def fake_bm25(query: str, limit: int) -> list[_ChunkRow]:
        seen_queries.append(query)
        if "Clawsy pricing Hetzner" in query:
            return [rows["clawsy"]]
        return [rows["studio"]]

    monkeypatch.setattr(engine, "_bm25", fake_bm25)
    monkeypatch.setattr(engine, "_vector", lambda query, limit: [rows["studio"]])
    monkeypatch.setattr(
        engine,
        "_rows_for_chunk_ids",
        lambda chunk_ids, score_map=None: [
            replace(rows[chunk_id], score=float((score_map or {}).get(chunk_id, 0.0))) for chunk_id in chunk_ids
        ],
    )

    response = engine.search(SearchReq(query="What's the pricing plan for Clawzy?", mode="hybrid", k=2))

    assert "projects/clawsy/state.md" in {row.path for row in response.results}
    assert seen_queries == ["What's the pricing plan for Clawzy?", "Clawsy pricing Hetzner"]


def test_hybrid_uses_retrieval_planner_queries_when_present(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder(), retrieval_planner=_FakeRetrievalPlanner())
    rows = {
        "clawsy": _ChunkRow(
            chunk_id="clawsy",
            path="projects/clawsy/state.md",
            content="Pricing: $19/mo BYOK. Hetzner CX22.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"clawsy","type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"warm"}',
            score=0.0,
        ),
        "studio": _ChunkRow(
            chunk_id="studio",
            path="projects/claws-studio/state.md",
            content="Hosted site plans.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"claws studio","type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"warm"}',
            score=0.0,
        ),
    }
    seen_queries: list[str] = []

    def fake_bm25(query: str, limit: int) -> list[_ChunkRow]:
        seen_queries.append(query)
        if "Clawsy pricing Hetzner" in query:
            return [rows["clawsy"]]
        return [rows["studio"]]

    monkeypatch.setattr(engine, "_bm25", fake_bm25)
    monkeypatch.setattr(engine, "_vector", lambda query, limit: [rows["studio"]])
    monkeypatch.setattr(
        engine,
        "_rows_for_chunk_ids",
        lambda chunk_ids, score_map=None: [
            replace(rows[chunk_id], score=float((score_map or {}).get(chunk_id, 0.0))) for chunk_id in chunk_ids
        ],
    )

    response = engine.search(SearchReq(query="What's the pricing plan for Clawzy?", mode="hybrid", k=2))

    assert "projects/clawsy/state.md" in {row.path for row in response.results}
    assert seen_queries == ["What's the pricing plan for Clawzy?", "Clawsy pricing Hetzner"]


def test_search_uses_planner_session_queries_for_durable_hybrid(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder(), retrieval_planner=_SessionAwarePlanner())
    durable_result = type(
        "Resp",
        (),
        {
            "query": "rooster focus",
            "count": 1,
            "results": [
                SearchResult(
                    path="core/active.md",
                    lines="1-1",
                    snippet="Rooster is the active focus this week.",
                    score=0.92,
                    frontmatter={},
                    stale_warning=None,
                )
            ],
            "took_ms": 5,
            "warnings": [],
        },
    )()
    session_result = type(
        "Resp",
        (),
        {
            "query": "rooster follow-up",
            "count": 1,
            "results": [
                SearchResult(
                    path="logs/sessions/claude/macbook/2026-04-12-s1.md",
                    lines="1-1",
                    snippet="Pricing follow-up is still open.",
                    score=0.71,
                    frontmatter={},
                    stale_warning=None,
                )
            ],
            "took_ms": 6,
            "warnings": [],
        },
    )()

    monkeypatch.setattr(
        engine, "_search_durable", lambda req, started, rerank_enabled, warnings, search_plan=None: durable_result
    )
    monkeypatch.setattr(engine, "_search_session_plane_multi", lambda queries, limit, started: session_result)

    response = engine.search(SearchReq(query="what are we working on today", mode="hybrid", corpus="durable", k=5))

    assert any(result.path.startswith("logs/sessions/") for result in response.results)


def test_search_can_reorder_results_with_result_selector(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder(), result_selector=_FakeResultSelector())
    durable_result = type(
        "Resp",
        (),
        {
            "query": "pricing",
            "count": 2,
            "results": [
                SearchResult(
                    path="projects/claws-studio/state.md",
                    lines="1-1",
                    snippet="Hosted site plans.",
                    score=0.88,
                    frontmatter={},
                    stale_warning=None,
                ),
                SearchResult(
                    path="projects/clawsy/state.md",
                    lines="1-1",
                    snippet="Pricing: $19/mo BYOK.",
                    score=0.81,
                    frontmatter={},
                    stale_warning=None,
                ),
            ],
            "took_ms": 5,
            "warnings": [],
        },
    )()

    monkeypatch.setattr(
        engine, "_search_durable", lambda req, started, rerank_enabled, warnings, search_plan=None: durable_result
    )
    monkeypatch.setattr(engine, "_should_fallback_to_session_plane", lambda req, response: False)

    response = engine.search(
        SearchReq(query="What's the pricing plan for Clawzy?", mode="hybrid", corpus="durable", k=2)
    )

    assert [result.path for result in response.results] == [
        "projects/clawsy/state.md",
        "projects/claws-studio/state.md",
    ]
    assert [result.rank_score for result in response.results] == [1.0, 0.0]


def test_search_reports_selection_warning_when_selector_fails(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder(), result_selector=_ExplodingResultSelector())
    durable_result = type(
        "Resp",
        (),
        {
            "query": "pricing",
            "count": 2,
            "results": [
                SearchResult(
                    path="projects/claws-studio/state.md",
                    lines="1-1",
                    snippet="Hosted site plans.",
                    score=0.88,
                    frontmatter={},
                    stale_warning=None,
                ),
                SearchResult(
                    path="projects/clawsy/state.md",
                    lines="1-1",
                    snippet="Pricing: $19/mo BYOK.",
                    score=0.81,
                    frontmatter={},
                    stale_warning=None,
                ),
            ],
            "took_ms": 5,
            "warnings": [],
        },
    )()

    monkeypatch.setattr(
        engine, "_search_durable", lambda req, started, rerank_enabled, warnings, search_plan=None: durable_result
    )
    monkeypatch.setattr(engine, "_should_fallback_to_session_plane", lambda req, response: False)

    response = engine.search(
        SearchReq(query="What's the pricing plan for Clawzy?", mode="hybrid", corpus="durable", k=2)
    )

    assert [result.path for result in response.results] == [
        "projects/claws-studio/state.md",
        "projects/clawsy/state.md",
    ]
    assert response.warnings == ["Result selection failed; search kept deterministic ranking."]


def test_hybrid_uses_expanded_queries_for_vector_recall(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder(), query_expander=_FakeQueryExpander())
    rows = {
        "clawsy": _ChunkRow(
            chunk_id="clawsy",
            path="projects/clawsy/state.md",
            content="Pricing: $19/mo BYOK. Hetzner CX22.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"clawsy","type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"warm"}',
            score=0.0,
        ),
        "studio": _ChunkRow(
            chunk_id="studio",
            path="projects/claws-studio/state.md",
            content="Hosted site plans.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"claws studio","type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"warm"}',
            score=0.0,
        ),
    }
    seen_vector_queries: list[str] = []

    monkeypatch.setattr(engine, "_bm25", lambda query, limit: [rows["studio"]])

    def fake_vector(query: str, limit: int) -> list[_ChunkRow]:
        seen_vector_queries.append(query)
        if "Clawsy pricing Hetzner" in query:
            return [rows["clawsy"]]
        return [rows["studio"]]

    monkeypatch.setattr(engine, "_vector", fake_vector)
    monkeypatch.setattr(
        engine,
        "_rows_for_chunk_ids",
        lambda chunk_ids, score_map=None: [
            replace(rows[chunk_id], score=float((score_map or {}).get(chunk_id, 0.0))) for chunk_id in chunk_ids
        ],
    )

    ranked = engine._hybrid("What's the pricing plan for Clawzy?", 2, warnings=[])

    assert "projects/clawsy/state.md" in {row.path for row in ranked}
    assert seen_vector_queries == ["What's the pricing plan for Clawzy?", "Clawsy pricing Hetzner"]


def test_hybrid_skips_expansion_for_current_state_queries(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder(), query_expander=_FakeQueryExpander())
    rows = {
        "core": _ChunkRow(
            chunk_id="core",
            path="core/env.md",
            content="Current default model: GPT-5.4.",
            start_line=1,
            end_line=3,
            frontmatter_json='{"title":"environment","type":"core","status":"active","canonical":true,"source_kind":"human","temperature":"hot"}',
            score=0.0,
        ),
        "old": _ChunkRow(
            chunk_id="old",
            path="logs/sessions/codex/2026-02-03-model-switch.md",
            content="Older model switch notes.",
            start_line=1,
            end_line=3,
            frontmatter_json='{"type":"session","status":"done","canonical":false,"source_kind":"human","temperature":"cold"}',
            score=0.0,
        ),
    }
    seen_queries: list[str] = []

    def fake_bm25(query: str, limit: int) -> list[_ChunkRow]:
        seen_queries.append(query)
        return [rows["core"], rows["old"]]

    monkeypatch.setattr(engine, "_bm25", fake_bm25)
    monkeypatch.setattr(engine, "_vector", lambda query, limit: [rows["core"], rows["old"]])
    monkeypatch.setattr(
        engine,
        "_rows_for_chunk_ids",
        lambda chunk_ids, score_map=None: [
            replace(rows[chunk_id], score=float((score_map or {}).get(chunk_id, 0.0))) for chunk_id in chunk_ids
        ],
    )

    ranked = engine._hybrid("What model is borb's default right now?", 2, warnings=[])

    assert ranked[0].path == "core/env.md"
    assert seen_queries == ["What model is borb's default right now?"]


def test_search_reports_query_expansion_warning_when_expander_fails(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder(), query_expander=_ExplodingQueryExpander())
    row = _ChunkRow(
        chunk_id="clawsy",
        path="projects/clawsy/state.md",
        content="Pricing: $19/mo BYOK. Hetzner CX22.",
        start_line=1,
        end_line=4,
        frontmatter_json='{"title":"clawsy","type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"warm"}',
        score=0.0,
    )

    monkeypatch.setattr(engine, "_bm25", lambda query, limit: [row])
    monkeypatch.setattr(engine, "_vector", lambda query, limit: [row])
    monkeypatch.setattr(
        engine,
        "_rows_for_chunk_ids",
        lambda chunk_ids, score_map=None: [
            replace(row, score=float((score_map or {}).get(chunk_id, 0.0))) for chunk_id in chunk_ids
        ],
    )
    monkeypatch.setattr(engine, "_should_expand", lambda profile, rows: True)

    response = engine.search(SearchReq(query="What's the pricing plan for Clawzy?", mode="hybrid", k=2))

    assert response.count == 1
    assert response.warnings == ["Query expansion failed; search used the base query only."]


def test_search_reorders_results_with_result_selector(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder(), result_selector=_FakeResultSelector())
    rows = {
        "studio": _ChunkRow(
            chunk_id="studio",
            path="projects/claws-studio/state.md",
            content="Hosted site plans.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"claws studio","type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"warm"}',
            score=0.9,
        ),
        "clawsy": _ChunkRow(
            chunk_id="clawsy",
            path="projects/clawsy/state.md",
            content="Pricing: $19/mo BYOK. Hetzner CX22.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"clawsy","type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"warm"}',
            score=0.8,
        ),
    }

    monkeypatch.setattr(
        engine,
        "_search_durable",
        lambda req, started, rerank_enabled, warnings, search_plan=None: type(
            "Resp",
            (),
            {
                "query": req.query,
                "count": 2,
                "results": [
                    SearchResult(
                        path=rows["studio"].path,
                        lines="1-4",
                        snippet="Hosted site plans.",
                        score=rows["studio"].score,
                        frontmatter={},
                        stale_warning=None,
                    ),
                    SearchResult(
                        path=rows["clawsy"].path,
                        lines="1-4",
                        snippet="Pricing: $19/mo BYOK. Hetzner CX22.",
                        score=rows["clawsy"].score,
                        frontmatter={},
                        stale_warning=None,
                    ),
                ],
                "took_ms": 5,
                "warnings": [],
            },
        )(),
    )

    response = engine.search(SearchReq(query="What's the pricing plan for Clawzy?", mode="hybrid", k=2))

    assert [result.path for result in response.results] == [
        "projects/clawsy/state.md",
        "projects/claws-studio/state.md",
    ]


def test_search_reports_warning_when_result_selector_fails(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder(), result_selector=_ExplodingResultSelector())
    monkeypatch.setattr(
        engine,
        "_search_durable",
        lambda req, started, rerank_enabled, warnings, search_plan=None: type(
            "Resp",
            (),
            {
                "query": req.query,
                "count": 2,
                "results": [
                    SearchResult(
                        path="projects/claws-studio/state.md",
                        lines="1-4",
                        snippet="Hosted site plans.",
                        score=0.9,
                        frontmatter={},
                        stale_warning=None,
                    ),
                    SearchResult(
                        path="projects/clawsy/state.md",
                        lines="1-4",
                        snippet="Pricing: $19/mo BYOK. Hetzner CX22.",
                        score=0.8,
                        frontmatter={},
                        stale_warning=None,
                    ),
                ],
                "took_ms": 5,
                "warnings": [],
            },
        )(),
    )

    response = engine.search(SearchReq(query="What's the pricing plan for Clawzy?", mode="hybrid", k=2))

    assert [result.path for result in response.results] == [
        "projects/claws-studio/state.md",
        "projects/clawsy/state.md",
    ]
    assert response.warnings == ["Result selection failed; search kept deterministic ranking."]


def test_hybrid_prefers_canonical_project_state_over_support_notes(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder())
    rows = {
        "state": _ChunkRow(
            chunk_id="state",
            path="projects/crawstr/state.md",
            content="Using official X API and WebSocket relay.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"hot"}',
            score=0.0,
        ),
        "support": _ChunkRow(
            chunk_id="support",
            path="projects/crawstr/notes-import/crawstr.md",
            content="Crawstr archived notes.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"type":"project","status":"done","canonical":false,"source_kind":"imported","temperature":"cold"}',
            score=0.0,
        ),
    }

    monkeypatch.setattr(engine, "_bm25", lambda query, limit: [rows["support"], rows["state"]])
    monkeypatch.setattr(engine, "_vector", lambda query, limit: [rows["support"], rows["state"]])
    monkeypatch.setattr(
        engine,
        "_rows_for_chunk_ids",
        lambda chunk_ids, score_map=None: [
            replace(rows[chunk_id], score=float((score_map or {}).get(chunk_id, 0.0))) for chunk_id in chunk_ids
        ],
    )

    ranked = engine._hybrid("What stack and API did we pick for Crawstr?", 2, warnings=[])

    assert ranked[0].path == "projects/crawstr/state.md"


def test_hybrid_prefers_canonical_decision_over_extracted_variant(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder())
    rows = {
        "canonical": _ChunkRow(
            chunk_id="canonical",
            path="decisions/2026-04-07-session-decisions-extracted.md",
            content="Canonical Dory decision summary.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"type":"decision","status":"done","canonical":true,"source_kind":"human","temperature":"warm"}',
            score=0.0,
        ),
        "extracted": _ChunkRow(
            chunk_id="extracted",
            path="decisions/extracted/2026-04-07-raw-session.md",
            content="Verbatim extracted decision text.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"type":"decision","status":"done","canonical":false,"source_kind":"extracted","temperature":"cold"}',
            score=0.0,
        ),
    }

    monkeypatch.setattr(engine, "_bm25", lambda query, limit: [rows["extracted"], rows["canonical"]])
    monkeypatch.setattr(engine, "_vector", lambda query, limit: [rows["extracted"], rows["canonical"]])
    monkeypatch.setattr(
        engine,
        "_rows_for_chunk_ids",
        lambda chunk_ids, score_map=None: [
            replace(rows[chunk_id], score=float((score_map or {}).get(chunk_id, 0.0))) for chunk_id in chunk_ids
        ],
    )

    ranked = engine._hybrid("What did we decide about Dory session extraction?", 2, warnings=[])

    assert ranked[0].path == "decisions/2026-04-07-session-decisions-extracted.md"


def test_hybrid_recovers_close_identifier_renames(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder())
    rows = {
        "clawsy": _ChunkRow(
            chunk_id="clawsy",
            path="projects/clawsy/state.md",
            content="Pricing: $19/mo BYOK. VPS: Hetzner CX22 per user.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"clawsy","type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"warm"}',
            score=0.0,
        ),
        "studio": _ChunkRow(
            chunk_id="studio",
            path="projects/claws-studio/state.md",
            content="Pricing work for agency plans and hosted sites.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"claws studio","type":"project","status":"active","canonical":true,"source_kind":"human","temperature":"warm"}',
            score=0.0,
        ),
    }

    monkeypatch.setattr(engine, "_bm25", lambda query, limit: [rows["studio"], rows["clawsy"]])
    monkeypatch.setattr(engine, "_vector", lambda query, limit: [rows["studio"], rows["clawsy"]])
    monkeypatch.setattr(
        engine,
        "_rows_for_chunk_ids",
        lambda chunk_ids, score_map=None: [
            replace(rows[chunk_id], score=float((score_map or {}).get(chunk_id, 0.0))) for chunk_id in chunk_ids
        ],
    )

    ranked = engine._hybrid("What's the pricing plan for Clawzy and what VPS is it meant to run on?", 2, warnings=[])

    assert ranked[0].path == "projects/clawsy/state.md"


def test_hybrid_prefers_temporal_daily_digest_over_general_soul_docs(monkeypatch, tmp_path: Path) -> None:
    engine = SearchEngine(tmp_path, _FakeEmbedder())
    rows = {
        "digest": _ChunkRow(
            chunk_id="digest",
            path="logs/daily/2026-02-09-digest.md",
            content="MD brain files cleaned up: SOUL.md deduplicated and trimmed in commit 3ec22f8.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"Mon (02/09)","type":"daily","date":"2026-02-09","status":"done","canonical":false,"source_kind":"human","temperature":"cold"}',
            score=0.0,
        ),
        "current": _ChunkRow(
            chunk_id="current",
            path="core/soul.md",
            content="Current SOUL rules and anti-opener preferences.",
            start_line=1,
            end_line=4,
            frontmatter_json='{"title":"SOUL","type":"core","status":"active","canonical":true,"source_kind":"human","temperature":"hot"}',
            score=0.0,
        ),
    }

    monkeypatch.setattr(engine, "_bm25", lambda query, limit: [rows["current"], rows["digest"]])
    monkeypatch.setattr(engine, "_vector", lambda query, limit: [rows["current"], rows["digest"]])
    monkeypatch.setattr(
        engine,
        "_rows_for_chunk_ids",
        lambda chunk_ids, score_map=None: [
            replace(rows[chunk_id], score=float((score_map or {}).get(chunk_id, 0.0))) for chunk_id in chunk_ids
        ],
    )

    ranked = engine._hybrid("When did we clean up SOUL.md and the other brain files?", 2, warnings=[])

    assert ranked[0].path == "logs/daily/2026-02-09-digest.md"
