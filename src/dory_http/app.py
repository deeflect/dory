from __future__ import annotations

import argparse
import logging
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    JSONResponse,
    Response,
)
import uvicorn

from dory_core.config import DorySettings, resolve_runtime_paths
from dory_core.embedding import (
    ContentEmbedder,
    EmbeddingConfigurationError,
    build_runtime_embedder,
)
from dory_core.llm.openrouter import build_openrouter_client
from dory_core.llm_rerank import build_reranker
from dory_core.migration_engine import MigrationEngine
from dory_core.migration_llm import MigrationLLM
from dory_core.query_expansion import OpenRouterQueryExpander
from dory_core.research import ResearchEngine
from dory_core.runtime import build_dory_runtime, build_query_expander, build_retrieval_planner
from dory_core.retrieval_planner import OpenRouterRetrievalPlanner
from dory_core.types import (
    MigrateReq,
)
from dory_http.api_routes import (
    register_api_routes,
)
from dory_http.auth import authorize_request
from dory_http.errors import (
    api_error_payload,
    reset_request_id,
    set_request_id,
)
from dory_http.runtime import HttpRuntime
from dory_http.web_routes import register_web_routes


_logger = logging.getLogger(__name__)
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
    runtime_embedder = embedder or build_runtime_embedder()
    surface_runtime = build_dory_runtime(
        corpus_root=Path(corpus_root),
        index_root=Path(index_root),
        settings=settings,
        embedder=runtime_embedder,
        reranker=build_reranker(settings),
        research_engine_cls=ResearchEngine,
    )
    runtime = HttpRuntime(
        corpus_root=Path(corpus_root),
        index_root=Path(index_root),
        auth_tokens_path=Path(auth_tokens_path) if auth_tokens_path is not None else None,
        allow_no_auth=settings.allow_no_auth,
        core=surface_runtime,
    )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> Response:
        # Wiki uses redirects/HTML and is not part of the JSON v1 contract.
        if request.url.path.startswith("/wiki"):
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers or None,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=api_error_payload(status_code=exc.status_code, detail=exc.detail),
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> Response:
        if request.url.path.startswith("/wiki"):
            return JSONResponse(status_code=422, content={"detail": exc.errors()})
        payload = api_error_payload(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": "Request payload failed validation.",
                "type": "validation_error",
                "errors": exc.errors(),
            },
        )
        return JSONResponse(status_code=422, content=payload)

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
        token = set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers["x-request-id"] = request_id
        return response

    register_web_routes(app, runtime, settings)

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    register_api_routes(app, runtime, settings)

    @app.post("/v1/migrate")
    def migrate(req: MigrateReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        result = _build_migration_engine(runtime, use_llm=req.use_llm).migrate(Path(req.legacy_root))
        return asdict(result)

    return app


def _authorize_request(request: Request, runtime: HttpRuntime) -> None:
    authorize_request(request, runtime.auth_tokens_path, allow_no_auth=runtime.allow_no_auth)


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
    return build_query_expander(settings)


def _build_retrieval_planner(settings: DorySettings, *, purpose: str) -> OpenRouterRetrievalPlanner | None:
    return build_retrieval_planner(settings, purpose=purpose)


if __name__ == "__main__":
    main()
