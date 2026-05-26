from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from dory_core.config import DorySettings
from dory_core.embedding import EmbeddingConfigurationError, EmbeddingProviderError
from dory_core.errors import DoryValidationError
from dory_core.index.reindex import reindex_corpus
from dory_core.openclaw_parity import OpenClawParityStore, list_public_artifacts
from dory_core.profiles import ProfileRegistry
from dory_core.session_ingest import SessionIngestService
from dory_core.status import build_status
from dory_core.types import (
    ActiveMemoryReq,
    DigestReq,
    LinkReq,
    MemoryProposalApplyReq,
    MemoryProposalCreateReq,
    MemoryProposalGetReq,
    MemoryProposalListReq,
    MemoryProposalRejectReq,
    MemoryWriteReq,
    PurgeReq,
    RecallEventReq,
    ResearchReq,
    SearchReq,
    SessionIngestReq,
    WakeReq,
    WriteReq,
    serialize_active_memory_response,
    serialize_search_response,
    serialize_wake_response,
)
from dory_http.auth import authorize_request
from dory_http.errors import raise_api_error, raise_http_error_for_runtime_value_error
from dory_http.metrics import render_metrics
from dory_http.runtime import HttpRuntime
from dory_mcp.tools import build_tool_schemas


def register_api_routes(app: FastAPI, runtime: HttpRuntime, settings: DorySettings) -> None:
    @app.post("/v1/wake")
    def wake(req: WakeReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            return serialize_wake_response(runtime.core.wake(req), debug=req.debug)
        except DoryValidationError as err:
            raise_api_error(
                status_code=400,
                code="dory_validation_error",
                message=str(err),
                error_type="validation",
                cause=err,
            )

    @app.post("/v1/search")
    def search(req: SearchReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            response = runtime.core.search(req)
            return serialize_search_response(response, debug=req.debug)
        except EmbeddingProviderError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err

    @app.post("/v1/digest")
    def digest(req: DigestReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            payload = runtime.core.digest(req).model_dump(mode="json")
        except ValueError as err:
            raise_api_error(
                status_code=400,
                code="bad_digest_selector",
                message=str(err),
                error_type="validation",
                cause=err,
            )
        if not req.debug:
            for field in ("frontmatter", "hash"):
                payload.pop(field, None)
        return payload

    @app.post("/v1/active-memory")
    def active_memory(req: ActiveMemoryReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            return serialize_active_memory_response(runtime.core.active_memory(req), debug=req.debug)
        except DoryValidationError as err:
            raise_api_error(
                status_code=400,
                code="dory_validation_error",
                message=str(err),
                error_type="validation",
                cause=err,
            )
        except EmbeddingProviderError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err

    @app.post("/v1/research")
    def research(req: ResearchReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            return runtime.core.research(req)
        except EmbeddingProviderError as err:
            raise HTTPException(status_code=503, detail=str(err)) from err

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
        try:
            return runtime.core.get(path, from_line=start_line, lines=lines, debug=debug)
        except ValueError as err:
            raise_http_error_for_runtime_value_error(err)

    @app.post("/v1/write")
    def write(req: WriteReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            return runtime.core.write(req).model_dump(mode="json")
        except DoryValidationError as err:
            raise_api_error(
                status_code=400,
                code="dory_validation_error",
                message=str(err),
                error_type="validation",
                cause=err,
            )
        except EmbeddingProviderError as err:
            raise_api_error(
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
            return runtime.core.purge(req).model_dump(mode="json")
        except DoryValidationError as err:
            raise_api_error(
                status_code=400,
                code="dory_validation_error",
                message=str(err),
                error_type="validation",
                cause=err,
            )
        except EmbeddingProviderError as err:
            raise_api_error(
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
            return runtime.core.memory_write(req).model_dump(mode="json")
        except DoryValidationError as err:
            raise_api_error(
                status_code=400,
                code="dory_validation_error",
                message=str(err),
                error_type="validation",
                cause=err,
            )
        except EmbeddingProviderError as err:
            raise_api_error(
                status_code=503,
                code="embedding_provider_error",
                message=str(err),
                error_type="backend",
                cause=err,
            )

    @app.post("/v1/memory-proposals")
    def memory_proposal_create(req: MemoryProposalCreateReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            return runtime.core.memory_propose(req)
        except DoryValidationError as err:
            raise_api_error(
                status_code=400,
                code="dory_validation_error",
                message=str(err),
                error_type="validation",
                cause=err,
            )
        except EmbeddingProviderError as err:
            raise_api_error(
                status_code=503,
                code="embedding_provider_error",
                message=str(err),
                error_type="backend",
                cause=err,
            )

    @app.get("/v1/memory-proposals")
    def memory_proposal_list(
        request: Request,
        status: str = Query("pending"),
    ) -> dict[str, Any]:
        _authorize_request(request, runtime)
        proposal_status = proposal_status_param(status)
        return runtime.core.memory_proposals(MemoryProposalListReq(status=proposal_status))

    @app.post("/v1/memory-proposals/list")
    def memory_proposal_list_post(req: MemoryProposalListReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        return runtime.core.memory_proposals(req)

    @app.get("/v1/memory-proposals/{proposal_id}")
    def memory_proposal_get(
        proposal_id: str,
        request: Request,
        status: str = Query("pending"),
    ) -> dict[str, Any]:
        _authorize_request(request, runtime)
        proposal_status = proposal_status_param(status)
        try:
            return runtime.core.memory_proposal_get(
                MemoryProposalGetReq(proposal_id=proposal_id, status=proposal_status)
            )
        except DoryValidationError as err:
            raise_api_error(
                status_code=404,
                code="proposal_not_found",
                message=str(err),
                error_type="not_found",
                cause=err,
            )

    @app.post("/v1/memory-proposals/get")
    def memory_proposal_get_post(req: MemoryProposalGetReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        try:
            return runtime.core.memory_proposal_get(req)
        except DoryValidationError as err:
            raise_api_error(
                status_code=404,
                code="proposal_not_found",
                message=str(err),
                error_type="not_found",
                cause=err,
            )

    @app.post("/v1/memory-proposals/apply")
    def memory_proposal_apply(req: MemoryProposalApplyReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        return apply_memory_proposal_req(req, runtime)

    @app.post("/v1/memory-proposals/{proposal_id}/apply")
    def memory_proposal_apply_path(proposal_id: str, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        return apply_memory_proposal_req(MemoryProposalApplyReq(proposal_id=proposal_id), runtime)

    @app.post("/v1/memory-proposals/reject")
    def memory_proposal_reject(req: MemoryProposalRejectReq, request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        return reject_memory_proposal_req(req, runtime)

    @app.post("/v1/memory-proposals/{proposal_id}/reject")
    def memory_proposal_reject_path(
        proposal_id: str,
        request: Request,
        reason: str | None = Query(None),
    ) -> dict[str, Any]:
        _authorize_request(request, runtime)
        return reject_memory_proposal_req(MemoryProposalRejectReq(proposal_id=proposal_id, reason=reason), runtime)

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
        try:
            return runtime.core.link(req)
        except ValueError as err:
            raise_http_error_for_runtime_value_error(err)

    @app.get("/v1/status")
    def status(request: Request, debug: bool = Query(False)) -> dict[str, Any]:
        _authorize_request(request, runtime)
        return runtime.core.status(debug=debug)

    @app.get("/v1/profiles")
    def profiles(request: Request) -> dict[str, Any]:
        _authorize_request(request, runtime)
        profile_list = ProfileRegistry(runtime.corpus_root).describe_profiles()
        return {"count": len(profile_list), "profiles": profile_list}

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
            yield _sse_event("status", runtime.core.status())
            if reindex:
                try:
                    if force and runtime.index_root.exists():
                        import shutil

                        shutil.rmtree(runtime.index_root)
                    result = reindex_corpus(runtime.corpus_root, runtime.index_root, runtime.core.embedder)
                    yield _sse_event("reindex", asdict(result))
                except (EmbeddingConfigurationError, EmbeddingProviderError) as err:
                    yield _sse_event("error", {"detail": str(err)})
            yield _sse_event("done", {"ok": True})

        return StreamingResponse(_events(), media_type="text/event-stream")


def apply_memory_proposal_req(req: MemoryProposalApplyReq, runtime: HttpRuntime) -> dict[str, Any]:
    try:
        return runtime.core.memory_proposal_apply(req)
    except DoryValidationError as err:
        raise_api_error(
            status_code=409,
            code="proposal_apply_failed",
            message=str(err),
            error_type="conflict",
            cause=err,
        )
    except EmbeddingProviderError as err:
        raise_api_error(
            status_code=503,
            code="embedding_provider_error",
            message=str(err),
            error_type="backend",
            cause=err,
        )


def reject_memory_proposal_req(req: MemoryProposalRejectReq, runtime: HttpRuntime) -> dict[str, Any]:
    try:
        return runtime.core.memory_proposal_reject(req)
    except DoryValidationError as err:
        raise_api_error(
            status_code=404,
            code="proposal_not_found",
            message=str(err),
            error_type="not_found",
            cause=err,
        )


def proposal_status_param(value: str) -> Literal["pending", "applied", "rejected"]:
    if value in {"pending", "applied", "rejected"}:
        return value  # type: ignore[return-value]
    raise HTTPException(status_code=400, detail="status must be pending, applied, or rejected")


def _authorize_request(request: Request, runtime: HttpRuntime) -> None:
    authorize_request(request, runtime.auth_tokens_path, allow_no_auth=runtime.allow_no_auth)


def _build_openclaw_parity_store(runtime: HttpRuntime) -> OpenClawParityStore:
    return OpenClawParityStore(runtime.index_root)


def _sse_event(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, sort_keys=True)}\n\n"
