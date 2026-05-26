from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any

from dory_core.active_memory import ActiveMemoryEngine
from dory_core.artifacts import ArtifactWriter
from dory_core.config import DorySettings
from dory_core.digests import DigestReader
from dory_core.dreaming.proposals import (
    ProposalStore,
    apply_proposal,
    create_semantic_write_proposal,
    proposal_to_payload,
    reject_proposal,
)
from dory_core.embedding import ContentEmbedder, build_runtime_embedder
from dory_core.features import DoryFeatureFlags
from dory_core.frontmatter import load_markdown_document
from dory_core.link import LinkService
from dory_core.llm.active_memory import build_active_memory_components
from dory_core.llm.openrouter import build_openrouter_client
from dory_core.llm_rerank import build_reranker
from dory_core.purge import PurgeEngine
from dory_core.query_expansion import OpenRouterQueryExpander
from dory_core.kernel_retrieval import KernelRetrievalEngine
from dory_core.research import ResearchEngine
from dory_core.retrieval import RetrievalFacade
from dory_core.retrieval_planner import OpenRouterRetrievalPlanner
from dory_core.search import SearchEngine
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.status import build_status, serialize_status
from dory_core.types import (
    ActiveMemoryReq,
    ActiveMemoryResp,
    DigestReq,
    DigestResp,
    LinkReq,
    MemoryProposalApplyReq,
    MemoryProposalCreateReq,
    MemoryProposalGetReq,
    MemoryProposalListReq,
    MemoryProposalRejectReq,
    MemoryWriteReq,
    MemoryWriteResp,
    PurgeReq,
    PurgeResp,
    ResearchReq,
    SearchReq,
    SearchResp,
    WakeReq,
    WakeResp,
    WriteReq,
    WriteResp,
)
from dory_core.wake import WakeBuilder
from dory_core.write import WriteEngine


@dataclass(frozen=True, slots=True)
class DoryRuntime:
    """Unified runtime for all Dory surfaces (HTTP, MCP, CLI).

    Consolidates components that each surface needs: embedder, search,
    query expansion, retrieval planning, reranking, active memory, and
    wake building.
    """

    corpus_root: Path
    index_root: Path
    settings: DorySettings
    features: DoryFeatureFlags
    embedder: ContentEmbedder
    query_expander: OpenRouterQueryExpander | None
    retrieval_planner: OpenRouterRetrievalPlanner | None
    reranker: Any
    rerank_candidate_limit: int
    research_engine_cls: Any
    retrieval: RetrievalFacade
    search_engine: SearchEngine
    active_memory_engine: ActiveMemoryEngine
    semantic_write_engine: SemanticWriteEngine
    wake_builder: WakeBuilder

    def wake(self, req: WakeReq) -> WakeResp:
        return self.wake_builder.build(req)

    def active_memory(self, req: ActiveMemoryReq) -> ActiveMemoryResp:
        return self.active_memory_engine.build(req)

    def search(self, req: SearchReq) -> SearchResp:
        return self.retrieval.search(req)

    def digest(self, req: DigestReq) -> DigestResp:
        return DigestReader(self.corpus_root).read(req)

    def get(
        self,
        path: str,
        *,
        from_line: int = 1,
        lines: int | None = None,
        debug: bool = False,
    ) -> dict[str, Any]:
        target = _resolve_corpus_path(self.corpus_root, path)
        text = target.read_text(encoding="utf-8")
        sliced = _slice_lines(text, from_line, lines)
        try:
            frontmatter = load_markdown_document(text).frontmatter
        except ValueError:
            frontmatter = {}
        payload: dict[str, Any] = {
            "path": path,
            "from": from_line,
            "lines_returned": len(sliced.splitlines()) if sliced else 0,
            "total_lines": len(text.splitlines()),
            "frontmatter": frontmatter,
            "hash": f"sha256:{sha256(text.encode('utf-8')).hexdigest()}",
            "content": sliced,
        }
        if debug:
            return payload
        for field in ("lines_returned", "total_lines", "frontmatter", "hash"):
            payload.pop(field, None)
        return payload

    def write(self, req: WriteReq) -> WriteResp:
        return WriteEngine(
            root=self.corpus_root,
            index_root=self.index_root,
            embedder=self.embedder,
        ).write(req)

    def memory_write(self, req: MemoryWriteReq) -> MemoryWriteResp:
        return self.semantic_write_engine.write(req)

    def memory_propose(self, req: MemoryProposalCreateReq) -> dict[str, Any]:
        proposal, path = create_semantic_write_proposal(
            root=self.corpus_root,
            engine=self.semantic_write_engine,
            req=req,
        )
        return {
            "proposal_id": proposal.proposal_id,
            "path": path.relative_to(self.corpus_root).as_posix(),
            "proposal": proposal_to_payload(proposal),
        }

    def memory_proposals(self, req: MemoryProposalListReq) -> dict[str, Any]:
        proposals = ProposalStore(self.corpus_root).list(status=req.status)
        return {"count": len(proposals), "proposals": proposals, "status": req.status}

    def memory_proposal_get(self, req: MemoryProposalGetReq) -> dict[str, Any]:
        proposal = ProposalStore(self.corpus_root).load(req.proposal_id, status=req.status)
        return proposal_to_payload(proposal)

    def memory_proposal_apply(self, req: MemoryProposalApplyReq) -> dict[str, Any]:
        result = apply_proposal(
            root=self.corpus_root,
            engine=self.semantic_write_engine,
            proposal_id=req.proposal_id,
            agent=req.agent,
            session_id=req.session_id,
            origin_surface=req.origin_surface,
        )
        return {
            "proposal_id": result.proposal_id,
            "applied": list(result.applied),
            "archived_path": result.archived_path,
        }

    def memory_proposal_reject(self, req: MemoryProposalRejectReq) -> dict[str, Any]:
        path = reject_proposal(root=self.corpus_root, proposal_id=req.proposal_id, reason=req.reason)
        return {"proposal_id": req.proposal_id, "path": path, "status": "rejected"}

    def purge(self, req: PurgeReq) -> PurgeResp:
        return PurgeEngine(
            root=self.corpus_root,
            index_root=self.index_root,
            embedder=self.embedder,
        ).purge(req)

    def link(self, req: LinkReq) -> dict[str, Any]:
        service = LinkService(self.corpus_root, self.index_root)
        if req.op == "neighbors":
            if req.path is None:
                raise ValueError("link neighbors requires path")
            path = _resolve_corpus_path(self.corpus_root, req.path).relative_to(self.corpus_root).as_posix()
            return service.neighbors(
                path,
                direction=req.direction,
                depth=req.depth,
                max_edges=req.max_edges,
                exclude_prefixes=req.exclude_prefixes,
            )
        if req.op == "backlinks":
            if req.path is None:
                raise ValueError("link backlinks requires path")
            path = _resolve_corpus_path(self.corpus_root, req.path).relative_to(self.corpus_root).as_posix()
            return service.backlinks(path, max_edges=req.max_edges, exclude_prefixes=req.exclude_prefixes)
        if req.op == "lint":
            return service.lint()
        raise ValueError(f"unsupported link op: {req.op}")

    def research(self, req: ResearchReq) -> dict[str, Any]:
        research_resp = self.research_engine_cls(self.retrieval).research_from_req(req)
        artifact_resp = None
        if req.save:
            artifact_resp = ArtifactWriter(
                self.corpus_root,
                index_root=self.index_root,
                embedder=self.embedder,
            ).write(
                research_resp.artifact,
                created=str(date.today()),
            )
        return {
            "artifact": artifact_resp.model_dump(mode="json") if artifact_resp is not None else None,
            "research": research_resp.model_dump(mode="json"),
        }

    def status(self, *, debug: bool = False) -> dict[str, Any]:
        return serialize_status(build_status(self.corpus_root, self.index_root, self.settings), debug=debug)


def build_dory_runtime(
    *,
    corpus_root: Path,
    index_root: Path,
    settings: DorySettings | None = None,
    embedder: ContentEmbedder | None = None,
    query_expander: OpenRouterQueryExpander | None = None,
    retrieval_planner: OpenRouterRetrievalPlanner | None = None,
    reranker: Any = None,
    rerank_candidate_limit: int | None = None,
    research_engine_cls: Any = None,
) -> DoryRuntime:
    resolved_settings = settings or DorySettings()
    features = DoryFeatureFlags.from_settings(resolved_settings)
    runtime_embedder = embedder or build_runtime_embedder()
    resolved_corpus_root = Path(corpus_root)
    resolved_index_root = Path(index_root)
    resolved_index_root.mkdir(parents=True, exist_ok=True)
    resolved_query_expander = query_expander if query_expander is not None else build_query_expander(resolved_settings)
    resolved_retrieval_planner = (
        retrieval_planner if retrieval_planner is not None else build_retrieval_planner(resolved_settings, purpose="query")
    )
    resolved_reranker = reranker if reranker is not None else build_reranker(resolved_settings)
    resolved_rerank_candidate_limit = (
        rerank_candidate_limit if rerank_candidate_limit is not None else resolved_settings.query_reranker_candidate_limit
    )
    resolved_research_engine_cls = research_engine_cls or ResearchEngine
    search_engine = SearchEngine(
        resolved_index_root,
        runtime_embedder,
        query_expander=resolved_query_expander,
        retrieval_planner=resolved_retrieval_planner,
        result_selector=resolved_retrieval_planner,
        reranker=resolved_reranker,
        rerank_candidate_limit=resolved_rerank_candidate_limit,
    )
    retrieval = RetrievalFacade(
        search_backend=search_engine,
        kernel_engine=KernelRetrievalEngine(
            root=resolved_corpus_root,
            search_engine=search_engine,
            link_service=LinkService(resolved_corpus_root, resolved_index_root),
        ),
    )
    wake_builder = WakeBuilder(resolved_corpus_root)
    active_memory_planner, active_memory_composer = build_active_memory_components(resolved_settings)
    active_memory_engine = ActiveMemoryEngine(
        wake_builder=wake_builder,
        search_engine=search_engine,
        root=resolved_corpus_root,
        planner=active_memory_planner,
        composer=active_memory_composer,
    )
    return DoryRuntime(
        corpus_root=resolved_corpus_root,
        index_root=resolved_index_root,
        settings=resolved_settings,
        features=features,
        embedder=runtime_embedder,
        query_expander=resolved_query_expander,
        retrieval_planner=resolved_retrieval_planner,
        reranker=resolved_reranker,
        rerank_candidate_limit=resolved_rerank_candidate_limit,
        research_engine_cls=resolved_research_engine_cls,
        retrieval=retrieval,
        search_engine=search_engine,
        active_memory_engine=active_memory_engine,
        semantic_write_engine=_LazySemanticWriteEngine(
            resolved_corpus_root,
            index_root=resolved_index_root,
            embedder=runtime_embedder,
        ),
        wake_builder=wake_builder,
    )


# ---------------------------------------------------------------------------
# Backward-compatible aliases
# ---------------------------------------------------------------------------
SurfaceRuntime = DoryRuntime
build_surface_runtime = build_dory_runtime


def build_query_expander(settings: DorySettings) -> OpenRouterQueryExpander | None:
    if not settings.query_expansion_enabled or settings.query_expansion_max <= 0:
        return None
    client = build_openrouter_client(settings, purpose="query")
    if client is None:
        return None
    return OpenRouterQueryExpander(client=client, max_expansions=settings.query_expansion_max)


def build_retrieval_planner(settings: DorySettings, *, purpose: str) -> OpenRouterRetrievalPlanner | None:
    if purpose == "query" and not settings.query_planner_enabled:
        return None
    client = build_openrouter_client(settings, purpose=purpose)
    if client is None:
        return None
    return OpenRouterRetrievalPlanner(client=client)


class _LazySemanticWriteEngine:
    def __init__(
        self,
        root: Path,
        *,
        index_root: Path | None,
        embedder: ContentEmbedder | None,
    ) -> None:
        self.root = Path(root)
        self.index_root = index_root
        self.embedder = embedder
        self._engine: SemanticWriteEngine | None = None

    def write(self, req: MemoryWriteReq) -> MemoryWriteResp:
        return self._load().write(req)

    def _load(self) -> SemanticWriteEngine:
        if self._engine is None:
            self._engine = SemanticWriteEngine(
                self.root,
                index_root=self.index_root,
                embedder=self.embedder,
            )
        return self._engine


def _resolve_corpus_path(corpus_root: Path, relative_path: str) -> Path:
    root = corpus_root.resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as err:
        raise ValueError(f"path escapes corpus root: {relative_path}") from err
    if not target.exists():
        raise ValueError(f"path not found: {relative_path}")
    return target


def _slice_lines(text: str, start_line: int, limit: int | None) -> str:
    if start_line < 1:
        raise ValueError("'from' must be >= 1")
    if limit is not None and limit < 1:
        raise ValueError("'lines' must be >= 1")
    lines = text.splitlines()
    start_index = start_line - 1
    end_index = len(lines) if limit is None else start_index + limit
    return "\n".join(lines[start_index:end_index])
