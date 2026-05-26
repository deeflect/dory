from __future__ import annotations

import argparse
import json
import socketserver
import sys
from dataclasses import dataclass, field
from io import TextIOBase
from pathlib import Path
from typing import Any, Protocol

from dory_core.config import DorySettings, resolve_runtime_paths
from dory_core.embedding import ContentEmbedder, EmbeddingConfigurationError, build_runtime_embedder
from dory_core.active_memory import ActiveMemoryEngine
from dory_core.query_expansion import OpenRouterQueryExpander
from dory_core.retrieval_planner import OpenRouterRetrievalPlanner
from dory_core.runtime import DoryRuntime, build_dory_runtime, build_query_expander, build_retrieval_planner
from dory_core.search import SearchEngine
from dory_core.semantic_write import SemanticWriteEngine
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
    ResearchReq,
    SearchReq,
    WakeReq,
    WriteReq,
    serialize_active_memory_response,
    serialize_search_response,
    serialize_wake_response,
)
from dory_core.wake import WakeBuilder
from dory_mcp.auth import (
    TcpAuthConfig,
    auth_error_response,
    extract_and_strip_auth,
    load_tcp_auth_config,
    token_matches,
)
from dory_mcp.tools import TOOL_MAP, build_tool_schemas


class DoryMcpCore(Protocol):
    def active_memory(self, req: Any) -> Any: ...

    def wake(self, req: Any) -> Any: ...

    def search(self, req: Any) -> Any: ...

    def digest(self, req: Any) -> Any: ...

    def get(self, req: Any) -> Any: ...

    def memory_write(self, req: Any) -> Any: ...

    def memory_propose(self, req: Any) -> Any: ...

    def memory_proposals(self, req: Any) -> Any: ...

    def memory_proposal_get(self, req: Any) -> Any: ...

    def memory_proposal_apply(self, req: Any) -> Any: ...

    def memory_proposal_reject(self, req: Any) -> Any: ...

    def write(self, req: Any) -> Any: ...

    def purge(self, req: Any) -> Any: ...

    def research(self, req: Any) -> Any: ...

    def link(self, req: Any) -> Any: ...

    def status(self, req: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class McpServeConfig:
    mode: str
    host: str
    port: int
    corpus_root: Path
    index_root: Path
    auth_tokens_path: Path | None
    allow_no_auth: bool


@dataclass(frozen=True, slots=True)
class RuntimeCore:
    corpus_root: Path
    index_root: Path
    embedder: ContentEmbedder
    query_expander: OpenRouterQueryExpander | None = None
    retrieval_planner: OpenRouterRetrievalPlanner | None = None
    reranker: Any = None
    rerank_candidate_limit: int = 40
    runtime: DoryRuntime = field(init=False)
    search_engine: SearchEngine = field(init=False)
    active_memory_engine: ActiveMemoryEngine = field(init=False)
    semantic_write_engine: SemanticWriteEngine = field(init=False)
    wake_builder: WakeBuilder = field(init=False)

    def __post_init__(self) -> None:
        dory_runtime: DoryRuntime = build_dory_runtime(
            corpus_root=self.corpus_root,
            index_root=self.index_root,
            embedder=self.embedder,
            query_expander=self.query_expander,
            retrieval_planner=self.retrieval_planner,
            reranker=self.reranker,
            rerank_candidate_limit=self.rerank_candidate_limit,
        )
        object.__setattr__(self, "runtime", dory_runtime)
        object.__setattr__(self, "search_engine", dory_runtime.search_engine)
        object.__setattr__(self, "active_memory_engine", dory_runtime.active_memory_engine)
        object.__setattr__(self, "semantic_write_engine", dory_runtime.semantic_write_engine)
        object.__setattr__(self, "wake_builder", dory_runtime.wake_builder)

    def wake(self, req: dict[str, Any]) -> Any:
        wake_req = WakeReq.model_validate(req)
        return serialize_wake_response(self.runtime.wake(wake_req), debug=wake_req.debug)

    def active_memory(self, req: dict[str, Any]) -> Any:
        active_req = ActiveMemoryReq.model_validate(req)
        response = self.runtime.active_memory(active_req)
        return serialize_active_memory_response(response, debug=active_req.debug)

    def research(self, req: dict[str, Any]) -> Any:
        return self.runtime.research(ResearchReq.model_validate(req))

    def search(self, req: dict[str, Any]) -> Any:
        search_req = SearchReq.model_validate(req)
        response = self.runtime.search(search_req)
        return serialize_search_response(response, debug=search_req.debug)

    def digest(self, req: dict[str, Any]) -> dict[str, Any]:
        digest_req = DigestReq.model_validate(req)
        payload = self.runtime.digest(digest_req).model_dump(mode="json")
        if not digest_req.debug:
            for field in ("frontmatter", "hash"):
                payload.pop(field, None)
        return payload

    def get(self, req: dict[str, Any]) -> dict[str, Any]:
        start_line = int(req.get("from", req.get("from_line", 1)))
        limit = req.get("lines")
        return self.runtime.get(
            str(req["path"]),
            from_line=start_line,
            lines=None if limit is None else int(limit),
            debug=bool(req.get("debug")),
        )

    def memory_write(self, req: dict[str, Any]) -> Any:
        return self.runtime.memory_write(MemoryWriteReq.model_validate(req))

    def memory_propose(self, req: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.memory_propose(MemoryProposalCreateReq.model_validate(req))

    def memory_proposals(self, req: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.memory_proposals(MemoryProposalListReq.model_validate(req))

    def memory_proposal_get(self, req: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.memory_proposal_get(MemoryProposalGetReq.model_validate(req))

    def memory_proposal_apply(self, req: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.memory_proposal_apply(MemoryProposalApplyReq.model_validate(req))

    def memory_proposal_reject(self, req: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.memory_proposal_reject(MemoryProposalRejectReq.model_validate(req))

    def write(self, req: dict[str, Any]) -> Any:
        return self.runtime.write(WriteReq.model_validate(req))

    def purge(self, req: dict[str, Any]) -> Any:
        return self.runtime.purge(PurgeReq.model_validate(req))

    def link(self, req: dict[str, Any]) -> dict[str, Any]:
        parsed = LinkReq.model_validate(req)
        return self.runtime.link(parsed)

    def status(self, req: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.status(debug=bool(req.get("debug")))


@dataclass(frozen=True, slots=True)
class DoryMcpServer:
    core: DoryMcpCore

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")

        if method == "tools/list":
            return self._ok(request_id, {"tools": build_tool_schemas()})
        if method == "tools/call":
            params = request.get("params") or {}
            try:
                return self._ok(request_id, self._call_tool(params))
            except Exception as err:
                return self._error(request_id, str(err), code=_error_code_for_exception(err))
        if method == "initialize":
            return self._ok(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "dory", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "initialized":
            return None

        return self._error(request_id, f"unsupported method: {method}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        verb = TOOL_MAP.get(tool_name)
        if verb is None:
            raise ValueError(f"unknown tool: {tool_name}")

        handler = getattr(self.core, verb, None)
        if handler is None:
            raise ValueError(f"core does not implement verb: {verb}")

        result = handler(arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": _render_result(result),
                }
            ]
        }

    @staticmethod
    def _ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, message: str, *, code: int = -32601) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def parse_error(message: str = "parse error") -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": message},
        }


def _error_code_for_exception(err: Exception) -> int:
    message = str(err)
    if "unknown tool" in message or "core does not implement verb" in message:
        return -32601
    if err.__class__.__name__ in {"ValidationError", "DoryValidationError"}:
        return -32602
    if isinstance(err, (TypeError, ValueError)):
        return -32602
    return -32603


def serve_stdio(
    core: DoryMcpCore,
    stdin: TextIOBase | None = None,
    stdout: TextIOBase | None = None,
) -> None:
    server = DoryMcpServer(core=core)
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout

    for raw_line in input_stream:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = server.parse_error()
            output_stream.write(json.dumps(response, sort_keys=True) + "\n")
            output_stream.flush()
            continue
        response = server.handle(request)
        if response is None:
            continue
        output_stream.write(json.dumps(response, sort_keys=True) + "\n")
        output_stream.flush()


class _TcpRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        server = self.server
        if not isinstance(server, DoryMcpTcpServer):
            raise TypeError("unexpected server type")

        # Per-connection latch: once a valid token is seen we don't require
        # clients to repeat it on every request over the same TCP connection.
        connection_authorized = not server.auth_config.required

        for raw_line in self.rfile:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = server.mcp_server.parse_error()
                self.wfile.write((json.dumps(response, sort_keys=True) + "\n").encode("utf-8"))
                self.wfile.flush()
                continue

            if not connection_authorized:
                token = extract_and_strip_auth(request)
                if not token_matches(token, server.auth_config):
                    response = auth_error_response(
                        request.get("id"),
                        message=(
                            "missing bearer token: pass it as params._auth.token on the first MCP request"
                            if not token
                            else "invalid bearer token"
                        ),
                    )
                    self.wfile.write((json.dumps(response, sort_keys=True) + "\n").encode("utf-8"))
                    self.wfile.flush()
                    continue
                connection_authorized = True
            else:
                # Tolerate repeated _auth fields after latch; just strip them.
                extract_and_strip_auth(request)

            response = server.mcp_server.handle(request)
            if response is None:
                continue
            self.wfile.write((json.dumps(response, sort_keys=True) + "\n").encode("utf-8"))
            self.wfile.flush()


class DoryMcpTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, core: DoryMcpCore, host: str, port: int, auth_config: TcpAuthConfig) -> None:
        self.mcp_server = DoryMcpServer(core=core)
        self.auth_config = auth_config
        super().__init__((host, port), _TcpRequestHandler)


def build_tcp_server(
    core: DoryMcpCore,
    host: str,
    port: int,
    *,
    auth_config: TcpAuthConfig | None = None,
    allow_no_auth: bool = False,
) -> DoryMcpTcpServer:
    """Build a TCP MCP server.

    Pass ``auth_config`` to enforce bearer auth, or ``allow_no_auth=True`` for
    process-local trusted contexts (tests, single-machine dev). Refusing to
    default to no-auth in production paths is intentional — the CLI entrypoint
    requires an explicit env opt-out.
    """
    if auth_config is None:
        auth_config = TcpAuthConfig(tokens=(), allow_no_auth=bool(allow_no_auth))
    return DoryMcpTcpServer(core=core, host=host, port=port, auth_config=auth_config)


def serve_tcp(
    core: DoryMcpCore,
    host: str,
    port: int,
    *,
    auth_config: TcpAuthConfig | None = None,
    allow_no_auth: bool = False,
) -> None:
    with build_tcp_server(
        core=core,
        host=host,
        port=port,
        auth_config=auth_config,
        allow_no_auth=allow_no_auth,
    ) as server:
        server.serve_forever()


def parse_serve_args(argv: list[str] | None = None) -> McpServeConfig:
    runtime_paths = resolve_runtime_paths()
    settings = DorySettings()
    parser = argparse.ArgumentParser(description="Run the Dory MCP bridge.")
    parser.add_argument("--mode", choices=["stdio", "tcp"], default="stdio", help="Transport mode")
    parser.add_argument("--host", default="127.0.0.1", help="TCP bind host")
    parser.add_argument("--port", type=int, default=8765, help="TCP bind port")
    parser.add_argument("--corpus-root", type=Path, default=runtime_paths.corpus_root, help="Path to the Dory corpus")
    parser.add_argument("--index-root", type=Path, default=runtime_paths.index_root, help="Path to the Dory index")
    parser.add_argument(
        "--auth-tokens-path",
        type=Path,
        default=runtime_paths.auth_tokens_path,
        help="Bearer-token file shared with the HTTP daemon (TCP mode only).",
    )
    parser.add_argument(
        "--allow-no-auth",
        action="store_true",
        default=settings.allow_no_auth,
        help="Disable bearer auth on the TCP listener. DO NOT enable on untrusted networks.",
    )
    args = parser.parse_args(argv)
    return McpServeConfig(
        mode=args.mode,
        host=args.host,
        port=args.port,
        corpus_root=args.corpus_root,
        index_root=args.index_root,
        auth_tokens_path=args.auth_tokens_path,
        allow_no_auth=args.allow_no_auth,
    )


def main(argv: list[str] | None = None) -> None:
    config = parse_serve_args(argv)
    try:
        core = RuntimeCore(
            corpus_root=config.corpus_root,
            index_root=config.index_root,
            embedder=build_runtime_embedder(),
        )
    except EmbeddingConfigurationError as err:
        raise SystemExit(str(err)) from err
    if config.mode == "tcp":
        try:
            auth_config = load_tcp_auth_config(
                auth_tokens_path=config.auth_tokens_path,
                allow_no_auth=config.allow_no_auth,
            )
        except ValueError as err:
            raise SystemExit(str(err)) from err
        serve_tcp(core=core, host=config.host, port=config.port, auth_config=auth_config)
        return
    serve_stdio(core=core)


def _render_result(result: Any) -> str:
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json()
    if isinstance(result, (dict, list)):
        return json.dumps(result, separators=(",", ":"), sort_keys=True)
    return str(result)


def _build_query_expander(settings) -> OpenRouterQueryExpander | None:
    return build_query_expander(settings)


def _build_retrieval_planner(settings: DorySettings, *, purpose: str) -> OpenRouterRetrievalPlanner | None:
    return build_retrieval_planner(settings, purpose=purpose)


if __name__ == "__main__":
    main()
