from __future__ import annotations

import argparse
import contextvars
import json
import logging
import uuid
from urllib.parse import parse_qs, urlencode
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any, NoReturn

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
import uvicorn

from dory_core.config import DorySettings, resolve_runtime_paths
from dory_core.embedding import (
    ContentEmbedder,
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    build_runtime_embedder,
)
from dory_core.errors import DoryValidationError
from dory_core.frontmatter import load_markdown_document
from dory_core.link import LinkService
from dory_core.llm.active_memory import build_active_memory_components
from dory_core.llm.openrouter import build_openrouter_client
from dory_core.llm_rerank import build_reranker
from dory_core.migration_engine import MigrationEngine
from dory_core.migration_llm import MigrationLLM
from dory_core.index.reindex import reindex_corpus
from dory_core.openclaw_parity import OpenClawParityStore, list_public_artifacts
from dory_core.purge import PurgeEngine
from dory_core.query_expansion import OpenRouterQueryExpander
from dory_core.retrieval_planner import OpenRouterRetrievalPlanner
from dory_core.artifacts import ArtifactWriter
from dory_core.research import ResearchEngine
from dory_core.search import SearchEngine
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.session_ingest import SessionIngestService
from dory_core.status import build_status, serialize_status
from dory_core.types import (
    ActiveMemoryReq,
    MigrateReq,
    LinkReq,
    RecallEventReq,
    MemoryWriteReq,
    ResearchReq,
    SearchReq,
    SessionIngestReq,
    WakeReq,
    PurgeReq,
    WriteReq,
    serialize_active_memory_response,
    serialize_search_response,
    serialize_wake_response,
)
from dory_core.active_memory import ActiveMemoryEngine
from dory_core.wake import WakeBuilder
from dory_core.write import WriteEngine
from dory_http.auth import (
    WEB_AUTH_COOKIE,
    WEB_SESSION_COOKIE,
    authorize_request,
    authorize_web_request,
    login_web_password,
)
from dory_http.metrics import render_metrics
from dory_http.wiki import render_wiki_login, render_wiki_page, render_wiki_search
from dory_mcp.tools import build_tool_schemas


_logger = logging.getLogger(__name__)
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dory_request_id", default=None
)


def current_request_id() -> str | None:
    """Return the request ID bound to the current FastAPI request, if any."""
    return _request_id_var.get()


def _raise_api_error(
    *,
    status_code: int,
    code: str,
    message: str,
    error_type: str,
    cause: Exception,
) -> NoReturn:
    detail: dict[str, str] = {
        "code": code,
        "message": message,
        "type": error_type,
    }
    request_id = current_request_id()
    if request_id is not None:
        detail["request_id"] = request_id
    raise HTTPException(status_code=status_code, detail=detail) from cause


@dataclass(frozen=True, slots=True)
class HttpRuntime:
    corpus_root: Path
    index_root: Path
    auth_tokens_path: Path | None
    allow_no_auth: bool
    embedder: ContentEmbedder
    query_expander: OpenRouterQueryExpander | None
    retrieval_planner: OpenRouterRetrievalPlanner | None
    reranker: Any
    rerank_candidate_limit: int


@dataclass(frozen=True, slots=True)
class ServeConfig:
    corpus_root: Path
    index_root: Path
    auth_tokens_path: Path | None
    host: str
    port: int


def build_app(
    corpus_root: Path,
    index_root: Path,
    auth_tokens_path: Path | None = None,
    embedder: ContentEmbedder | None = None,
) -> FastAPI:
    app = FastAPI()
    settings = DorySettings()
    runtime = HttpRuntime(
        corpus_root=Path(corpus_root),
        index_root=Path(index_root),
        auth_tokens_path=Path(auth_tokens_path) if auth_tokens_path is not None else None,
        allow_no_auth=settings.allow_no_auth,
        embedder=embedder or build_runtime_embedder(),
        query_expander=_build_query_expander(settings),
        retrieval_planner=_build_retrieval_planner(settings, purpose="query"),
        reranker=build_reranker(settings),
        rerank_candidate_limit=settings.query_reranker_candidate_limit,
    )

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "").strip()
        # Trust the caller-supplied value when it looks ID-shaped; otherwise mint one.
        # The bounds keep a rogue client from stuffing headers with arbitrary content
        # that would later land in logs.
        if incoming and len(incoming) <= 128 and all(ch.isalnum() or ch in "-_" for ch in incoming):
            request_id = incoming
        else:
            request_id = uuid.uuid4().hex
        token = _request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers["x-request-id"] = request_id
        return response

    @app.get("/wiki", response_class=HTMLResponse)
    def wiki_index(request: Request) -> Response:
        cookie_token = _authorize_wiki_or_redirect(request, runtime)
        if isinstance(cookie_token, RedirectResponse):
            return cookie_token
        response = render_wiki_page(runtime.corpus_root, "")
        _set_legacy_web_auth_cookie(response, request, cookie_token)
        return response

    @app.get("/wiki/login", response_class=HTMLResponse)
    def wiki_login(next: str = Query("/wiki")) -> HTMLResponse:
        return render_wiki_login(next_path=_safe_wiki_next(next))

    @app.post("/wiki/login")
    async def wiki_login_submit(request: Request) -> Response:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        password = form.get("password", [""])[0]
        next_path = _safe_wiki_next(form.get("next", ["/wiki"])[0])
        try:
            login = login_web_password(password)
        except HTTPException as err:
            if err.status_code == 401:
                return render_wiki_login(
                    next_path=next_path,
                    error="Invalid password.",
                    status_code=401,
                )
            raise
        response = RedirectResponse(next_path, status_code=303)
        _set_web_session_cookie(response, request, login.session_cookie)
        return response

    @app.get("/wiki/logout")
    def wiki_logout() -> Response:
        response = RedirectResponse("/wiki/login", status_code=303)
        response.delete_cookie(WEB_AUTH_COOKIE)
        response.delete_cookie(WEB_SESSION_COOKIE)
        return response

    @app.get("/wiki/search", response_class=HTMLResponse)
    def wiki_search(
        request: Request,
        q: str = Query(""),
    ) -> Response:
        cookie_token = _authorize_wiki_or_redirect(request, runtime)
        if isinstance(cookie_token, RedirectResponse):
            return cookie_token
        response = render_wiki_search(runtime.corpus_root, q)
        _set_legacy_web_auth_cookie(response, request, cookie_token)
        return response

    @app.get("/wiki/{page:path}", response_class=HTMLResponse)
    def wiki_page(page: str, request: Request) -> Response:
        cookie_token = _authorize_wiki_or_redirect(request, runtime)
        if isinstance(cookie_token, RedirectResponse):
            return cookie_token
        response = render_wiki_page(runtime.corpus_root, page)
        _set_legacy_web_auth_cookie(response, request, cookie_token)
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/wake")
    def wake(req: WakeReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        return serialize_wake_response(WakeBuilder(runtime.corpus_root).build(req), debug=req.debug)

    @app.post("/v1/search")
    def search(req: SearchReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            response = SearchEngine(
                runtime.index_root,
                runtime.embedder,
                query_expander=runtime.query_expander,
                retrieval_planner=runtime.retrieval_planner,
                result_selector=runtime.retrieval_planner,
                reranker=runtime.reranker,
                rerank_candidate_limit=runtime.rerank_candidate_limit,
            ).search(req)
            return serialize_search_response(response, debug=req.debug)
        except EmbeddingProviderError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err

    @app.post("/v1/active-memory")
    def active_memory(req: ActiveMemoryReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            return serialize_active_memory_response(_build_active_memory_engine(runtime).build(req), debug=req.debug)
        except EmbeddingProviderError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err

    @app.post("/v1/research")
    def research(req: ResearchReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            research_resp = ResearchEngine(
                search_engine=SearchEngine(
                    runtime.index_root,
                    runtime.embedder,
                    query_expander=runtime.query_expander,
                    retrieval_planner=runtime.retrieval_planner,
                    result_selector=runtime.retrieval_planner,
                    reranker=runtime.reranker,
                    rerank_candidate_limit=runtime.rerank_candidate_limit,
                )
            ).research_from_req(req)
            artifact_resp = None
            if req.save:
                artifact_resp = ArtifactWriter(
                    runtime.corpus_root,
                    index_root=runtime.index_root,
                    embedder=runtime.embedder,
                ).write(
                    research_resp.artifact,
                    created=str(date.today()),
                )
        except EmbeddingProviderError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err
        return {
            "artifact": artifact_resp.model_dump(mode="json") if artifact_resp is not None else None,
            "research": research_resp.model_dump(mode="json"),
        }

    @app.post("/v1/migrate")
    def migrate(req: MigrateReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        result = _build_migration_engine(runtime, use_llm=req.use_llm).migrate(Path(req.legacy_root))
        return asdict(result)

    @app.get("/v1/get")
    def get(
        request: Request,
        path: str = Query(...),
        from_line: int | None = Query(None, alias="from"),
        legacy_from_line: int | None = Query(None, alias="from_line"),
        lines: int | None = Query(None),
        debug: bool = Query(False),
    ) -> dict[str, Any]:
        _authorize_request(request, runtime)
        start_line = from_line if from_line is not None else legacy_from_line if legacy_from_line is not None else 1
        target = _resolve_corpus_path(runtime.corpus_root, path)
        text = target.read_text(encoding="utf-8")
        sliced = _slice_lines(text, start_line, lines)
        frontmatter: dict[str, object] = {}
        try:
            frontmatter = load_markdown_document(text).frontmatter
        except ValueError:
            frontmatter = {}
        payload = {
            "path": path,
            "from": start_line,
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

    @app.post("/v1/write")
    def write(req: WriteReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            return (
                WriteEngine(
                    root=runtime.corpus_root,
                    index_root=runtime.index_root,
                    embedder=runtime.embedder,
                )
                .write(req)
                .model_dump(mode="json")
            )
        except DoryValidationError as err:
            _raise_api_error(
                status_code=400,
                code="dory_validation_error",
                message=str(err),
                error_type="validation",
                cause=err,
            )
        except EmbeddingProviderError as err:
            _raise_api_error(
                status_code=503,
                code="embedding_provider_error",
                message=str(err),
                error_type="backend",
                cause=err,
            )

    @app.post("/v1/purge")
    def purge(req: PurgeReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            return (
                PurgeEngine(
                    root=runtime.corpus_root,
                    index_root=runtime.index_root,
                    embedder=runtime.embedder,
                )
                .purge(req)
                .model_dump(mode="json")
            )
        except DoryValidationError as err:
            _raise_api_error(
                status_code=400,
                code="dory_validation_error",
                message=str(err),
                error_type="validation",
                cause=err,
            )
        except EmbeddingProviderError as err:
            _raise_api_error(
                status_code=503,
                code="embedding_provider_error",
                message=str(err),
                error_type="backend",
                cause=err,
            )

    @app.post("/v1/memory-write")
    def memory_write(req: MemoryWriteReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            return _build_semantic_write_engine(runtime).write(req).model_dump(mode="json")
        except DoryValidationError as err:
            _raise_api_error(
                status_code=400,
                code="dory_validation_error",
                message=str(err),
                error_type="validation",
                cause=err,
            )
        except EmbeddingProviderError as err:
            _raise_api_error(
                status_code=503,
                code="embedding_provider_error",
                message=str(err),
                error_type="backend",
                cause=err,
            )

    @app.post("/v1/recall-event")
    def recall_event(req: RecallEventReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        return _build_openclaw_parity_store(runtime).record_recall_event(req).model_dump(mode="json")

    @app.get("/v1/public-artifacts")
    def public_artifacts(request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        artifacts = list_public_artifacts(runtime.corpus_root)
        return {
            "count": len(artifacts),
            "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        }

    @app.post("/v1/session-ingest")
    def session_ingest(req: SessionIngestReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            return (
                SessionIngestService(
                    corpus_root=runtime.corpus_root,
                    session_db_path=runtime.index_root / "session_plane.db",
                )
                .ingest(req)
                .model_dump(mode="json")
            )
        except DoryValidationError as err:
            raise HTTPException(status_code=400, detail=str(err)) from err

    @app.post("/v1/link")
    def link(req: LinkReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        service = LinkService(runtime.corpus_root, runtime.index_root)
        if req.op == "neighbors":
            if req.path is None:
                raise HTTPException(status_code=400, detail="link neighbors requires path")
            normalized_path = _resolve_corpus_path(runtime.corpus_root, req.path).relative_to(runtime.corpus_root)
            return service.neighbors(
                normalized_path.as_posix(),
                direction=req.direction,
                depth=req.depth,
                max_edges=req.max_edges,
                exclude_prefixes=req.exclude_prefixes,
            )
        if req.op == "backlinks":
            if req.path is None:
                raise HTTPException(status_code=400, detail="link backlinks requires path")
            normalized_path = _resolve_corpus_path(runtime.corpus_root, req.path).relative_to(runtime.corpus_root)
            return service.backlinks(
                normalized_path.as_posix(),
                max_edges=req.max_edges,
                exclude_prefixes=req.exclude_prefixes,
            )
        if req.op == "lint":
            return service.lint()
        raise HTTPException(status_code=400, detail=f"unsupported link op: {req.op}")

    @app.get("/v1/status")
    def status(request: Request, debug: bool = Query(False)) -> dict[str, Any]:
        _authorize_request(request, runtime)
        return serialize_status(build_status(runtime.corpus_root, runtime.index_root, settings), debug=debug)

    @app.get("/v1/tools")
    def tools(request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        return {"tools": build_tool_schemas()}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(request: Request) -> str:
        _authorize_request(request, runtime)
        return render_metrics(build_status(runtime.corpus_root, runtime.index_root, settings))

    @app.get("/v1/stream")
    def stream(
        request: Request,
        reindex: bool = Query(False),
        force: bool = Query(False),
    ) -> StreamingResponse:
        _authorize_request(request, runtime)

        def _events() -> str:
            yield _sse_event("status", serialize_status(build_status(runtime.corpus_root, runtime.index_root, settings)))
            if reindex:
                try:
                    if force and runtime.index_root.exists():
                        import shutil

                        shutil.rmtree(runtime.index_root)
                    result = reindex_corpus(runtime.corpus_root, runtime.index_root, runtime.embedder)
                    yield _sse_event("reindex", asdict(result))
                except (EmbeddingConfigurationError, EmbeddingProviderError) as err:
                    yield _sse_event("error", {"detail": str(err)})
            yield _sse_event("done", {"ok": True})

        return StreamingResponse(_events(), media_type="text/event-stream")

    return app


def _resolve_corpus_path(corpus_root: Path, relative_path: str) -> Path:
    root = corpus_root.resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=f"path escapes corpus root: {relative_path}") from err
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"path not found: {relative_path}")
    return target


def _slice_lines(text: str, start_line: int, limit: int | None) -> str:
    if start_line < 1:
        raise HTTPException(status_code=400, detail="'from' must be >= 1")
    if limit is not None and limit < 1:
        raise HTTPException(status_code=400, detail="'lines' must be >= 1")
    lines = text.splitlines()
    start_index = start_line - 1
    end_index = len(lines) if limit is None else start_index + limit
    return "\n".join(lines[start_index:end_index])


def _sse_event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, sort_keys=True)}\n\n"


def _authorize_wiki_or_redirect(
    request: Request,
    runtime: HttpRuntime,
) -> str | RedirectResponse | None:
    try:
        return authorize_web_request(
            request,
            runtime.auth_tokens_path,
            allow_no_auth=runtime.allow_no_auth,
        )
    except HTTPException as err:
        if err.status_code != 401:
            raise
        next_path = _safe_wiki_next(_request_wiki_next(request))
        return RedirectResponse(
            f"/wiki/login?{urlencode({'next': next_path})}",
            status_code=303,
        )


def _request_wiki_next(request: Request) -> str:
    query = [(key, value) for key, value in request.query_params.multi_items() if key != "token"]
    if not query:
        return request.url.path
    return f"{request.url.path}?{urlencode(query)}"


def _safe_wiki_next(next_path: str) -> str:
    if next_path.startswith("/wiki") and not next_path.startswith("//"):
        return next_path
    return "/wiki"


def _authorize_request(request: Request, runtime: HttpRuntime) -> None:
    authorize_request(request, runtime.auth_tokens_path, allow_no_auth=runtime.allow_no_auth)


def _set_legacy_web_auth_cookie(response: Response, request: Request, token: str | None) -> None:
    if token is None:
        return
    response.set_cookie(
        WEB_AUTH_COOKIE,
        token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )


def _set_web_session_cookie(response: Response, request: Request, session_cookie: str) -> None:
    response.set_cookie(
        WEB_SESSION_COOKIE,
        session_cookie,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )


def _build_semantic_write_engine(runtime: HttpRuntime) -> SemanticWriteEngine:
    return SemanticWriteEngine(
        runtime.corpus_root,
        index_root=runtime.index_root,
        embedder=runtime.embedder,
    )


def _build_openclaw_parity_store(runtime: HttpRuntime) -> OpenClawParityStore:
    return OpenClawParityStore(runtime.index_root)


def _build_migration_engine(runtime: HttpRuntime, *, use_llm: bool = True) -> MigrationEngine:
    if not use_llm:
        return MigrationEngine(runtime.corpus_root, llm=None)
    settings = DorySettings()
    client = build_openrouter_client(settings, purpose="maintenance")
    llm = MigrationLLM(client=client) if client is not None else None
    return MigrationEngine(runtime.corpus_root, llm=llm)


def parse_serve_args(argv: list[str] | None = None) -> ServeConfig:
    settings = DorySettings()
    parser = argparse.ArgumentParser(description="Run the Dory HTTP server.")
    parser.add_argument("--corpus-root", type=Path, default=None, help="Path to the Dory corpus")
    parser.add_argument("--index-root", type=Path, default=None, help="Path to the Dory index")
    parser.add_argument(
        "--auth-tokens-path",
        type=Path,
        default=None,
        help="Path to the optional bearer token store",
    )
    parser.add_argument("--host", default=settings.http_host, help="Bind host")
    parser.add_argument("--port", type=int, default=settings.http_port, help="Bind port")
    args = parser.parse_args(argv)
    runtime_paths = resolve_runtime_paths(
        corpus_root=args.corpus_root,
        index_root=args.index_root,
        auth_tokens_path=args.auth_tokens_path,
    )
    return ServeConfig(
        corpus_root=runtime_paths.corpus_root,
        index_root=runtime_paths.index_root,
        auth_tokens_path=runtime_paths.auth_tokens_path,
        host=args.host,
        port=args.port,
    )


def main(argv: list[str] | None = None) -> None:
    config = parse_serve_args(argv)
    try:
        app = build_app(
            corpus_root=config.corpus_root,
            index_root=config.index_root,
            auth_tokens_path=config.auth_tokens_path,
        )
    except EmbeddingConfigurationError as err:
        raise SystemExit(str(err)) from err
    uvicorn.run(app, host=config.host, port=config.port)


def _build_query_expander(settings: DorySettings) -> OpenRouterQueryExpander | None:
    if not settings.query_expansion_enabled or settings.query_expansion_max <= 0:
        return None
    client = build_openrouter_client(settings, purpose="query")
    if client is None:
        return None
    return OpenRouterQueryExpander(client=client, max_expansions=settings.query_expansion_max)


def _build_retrieval_planner(settings: DorySettings, *, purpose: str) -> OpenRouterRetrievalPlanner | None:
    if purpose == "query" and not settings.query_planner_enabled:
        return None
    client = build_openrouter_client(settings, purpose=purpose)
    if client is None:
        return None
    return OpenRouterRetrievalPlanner(client=client)


def _build_active_memory_engine(runtime: HttpRuntime) -> ActiveMemoryEngine:
    planner, composer = build_active_memory_components(DorySettings())
    return ActiveMemoryEngine(
        wake_builder=WakeBuilder(runtime.corpus_root),
        search_engine=SearchEngine(
            runtime.index_root,
            runtime.embedder,
            query_expander=runtime.query_expander,
            retrieval_planner=runtime.retrieval_planner,
            result_selector=runtime.retrieval_planner,
            reranker=runtime.reranker,
            rerank_candidate_limit=runtime.rerank_candidate_limit,
        ),
        root=runtime.corpus_root,
        planner=planner,
        composer=composer,
    )


if __name__ == "__main__":
    main()
