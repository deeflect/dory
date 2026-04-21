from __future__ import annotations

import argparse
import json
import socketserver
import sys
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import TextIOBase
from pathlib import Path
from typing import Any, Protocol

from dory_core.config import DorySettings, resolve_runtime_paths
from dory_core.embedding import ContentEmbedder, EmbeddingConfigurationError, build_runtime_embedder
from dory_core.active_memory import ActiveMemoryEngine
from dory_core.artifacts import ArtifactWriter
from dory_core.frontmatter import load_markdown_document
from dory_core.link import LinkService
from dory_core.llm.active_memory import build_active_memory_components
from dory_core.llm.openrouter import build_openrouter_client
from dory_core.llm_rerank import build_reranker
from dory_core.purge import PurgeEngine
from dory_core.query_expansion import OpenRouterQueryExpander
from dory_core.retrieval_planner import OpenRouterRetrievalPlanner
from dory_core.research import ResearchEngine
from dory_core.search import SearchEngine
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.status import build_status, serialize_status
from dory_core.types import (
    ActiveMemoryReq,
    LinkReq,
    MemoryWriteReq,
    PurgeReq,
    ResearchReq,
    SearchReq,
    WakeReq,
    WriteReq,
    serialize_search_response,
)
from dory_core.wake import WakeBuilder
from dory_core.write import WriteEngine
from dory_mcp.tools import TOOL_MAP, build_tool_schemas


class DoryMcpCore(Protocol):
    def active_memory(self, req: Any) -> Any: ...

    def wake(self, req: Any) -> Any: ...

    def search(self, req: Any) -> Any: ...

    def get(self, req: Any) -> Any: ...

    def memory_write(self, req: Any) -> Any: ...

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


@dataclass(frozen=True, slots=True)
class RuntimeCore:
    corpus_root: Path
    index_root: Path
    embedder: ContentEmbedder
    query_expander: OpenRouterQueryExpander | None = None
    retrieval_planner: OpenRouterRetrievalPlanner | None = None
    reranker: Any = None
    rerank_candidate_limit: int = 40

    def wake(self, req: dict[str, Any]) -> Any:
        return WakeBuilder(self.corpus_root).build(WakeReq.model_validate(req))

    def active_memory(self, req: dict[str, Any]) -> Any:
        planner, composer = build_active_memory_components(DorySettings())
        return ActiveMemoryEngine(
            wake_builder=WakeBuilder(self.corpus_root),
            search_engine=SearchEngine(
                self.index_root,
                self.embedder,
                query_expander=self.query_expander,
                retrieval_planner=self.retrieval_planner,
                result_selector=self.retrieval_planner,
                reranker=self.reranker,
                rerank_candidate_limit=self.rerank_candidate_limit,
            ),
            root=self.corpus_root,
            planner=planner,
            composer=composer,
        ).build(ActiveMemoryReq.model_validate(req))

    def research(self, req: dict[str, Any]) -> Any:
        research_resp = ResearchEngine(
            search_engine=SearchEngine(
                self.index_root,
                self.embedder,
                query_expander=self.query_expander,
                retrieval_planner=self.retrieval_planner,
                result_selector=self.retrieval_planner,
                reranker=self.reranker,
                rerank_candidate_limit=self.rerank_candidate_limit,
            )
        ).research_from_req(ResearchReq.model_validate(req))
        artifact_resp = None
        if req.get("save", True):
            artifact_resp = ArtifactWriter(
                self.corpus_root,
                index_root=self.index_root,
                embedder=self.embedder,
            ).write(
                research_resp.artifact,
                created=str(date.today()),
            )
        return {
            "artifact": artifact_resp if artifact_resp is None else artifact_resp.model_dump(),
            "research": research_resp.model_dump(),
        }

    def search(self, req: dict[str, Any]) -> Any:
        search_req = SearchReq.model_validate(req)
        response = SearchEngine(
            self.index_root,
            self.embedder,
            query_expander=self.query_expander,
            retrieval_planner=self.retrieval_planner,
            result_selector=self.retrieval_planner,
            reranker=self.reranker,
            rerank_candidate_limit=self.rerank_candidate_limit,
        ).search(search_req)
        return serialize_search_response(response, debug=search_req.debug)

    def get(self, req: dict[str, Any]) -> dict[str, Any]:
        path = _resolve_corpus_path(self.corpus_root, str(req["path"]))
        text = path.read_text(encoding="utf-8")
        start_line = int(req.get("from", 1))
        limit = req.get("lines")
        sliced = _slice_lines(text, start_line, None if limit is None else int(limit))
        try:
            frontmatter = load_markdown_document(text).frontmatter
        except ValueError:
            frontmatter = {}
        return {
            "path": str(req["path"]),
            "from": start_line,
            "lines_returned": len(sliced.splitlines()) if sliced else 0,
            "total_lines": len(text.splitlines()),
            "frontmatter": frontmatter,
            "hash": f"sha256:{sha256(text.encode('utf-8')).hexdigest()}",
            "content": sliced,
        }

    def memory_write(self, req: dict[str, Any]) -> Any:
        return SemanticWriteEngine(
            self.corpus_root,
            index_root=self.index_root,
            embedder=self.embedder,
        ).write(MemoryWriteReq.model_validate(req))

    def write(self, req: dict[str, Any]) -> Any:
        return WriteEngine(
            root=self.corpus_root,
            index_root=self.index_root,
            embedder=self.embedder,
        ).write(WriteReq.model_validate(req))

    def purge(self, req: dict[str, Any]) -> Any:
        return PurgeEngine(
            root=self.corpus_root,
            index_root=self.index_root,
            embedder=self.embedder,
        ).purge(PurgeReq.model_validate(req))

    def link(self, req: dict[str, Any]) -> dict[str, Any]:
        service = LinkService(self.corpus_root, self.index_root)
        parsed = LinkReq.model_validate(req)
        if parsed.op == "neighbors":
            if parsed.path is None:
                raise ValueError("link neighbors requires path")
            path = _resolve_corpus_path(self.corpus_root, parsed.path).relative_to(self.corpus_root).as_posix()
            return service.neighbors(
                path,
                direction=parsed.direction,
                depth=parsed.depth,
                max_edges=parsed.max_edges,
                exclude_prefixes=parsed.exclude_prefixes,
            )
        if parsed.op == "backlinks":
            if parsed.path is None:
                raise ValueError("link backlinks requires path")
            path = _resolve_corpus_path(self.corpus_root, parsed.path).relative_to(self.corpus_root).as_posix()
            return service.backlinks(path, max_edges=parsed.max_edges, exclude_prefixes=parsed.exclude_prefixes)
        if parsed.op == "lint":
            return service.lint()
        raise ValueError(f"unsupported link op: {parsed.op}")

    def status(self, req: dict[str, Any]) -> dict[str, Any]:
        del req
        return serialize_status(build_status(self.corpus_root, self.index_root))


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
            response = server.mcp_server.handle(request)
            if response is None:
                continue
            self.wfile.write((json.dumps(response, sort_keys=True) + "\n").encode("utf-8"))
            self.wfile.flush()


class DoryMcpTcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, core: DoryMcpCore, host: str, port: int) -> None:
        self.mcp_server = DoryMcpServer(core=core)
        super().__init__((host, port), _TcpRequestHandler)


def build_tcp_server(core: DoryMcpCore, host: str, port: int) -> DoryMcpTcpServer:
    return DoryMcpTcpServer(core=core, host=host, port=port)


def serve_tcp(core: DoryMcpCore, host: str, port: int) -> None:
    with build_tcp_server(core=core, host=host, port=port) as server:
        server.serve_forever()


def parse_serve_args(argv: list[str] | None = None) -> McpServeConfig:
    runtime_paths = resolve_runtime_paths()
    parser = argparse.ArgumentParser(description="Run the Dory MCP bridge.")
    parser.add_argument("--mode", choices=["stdio", "tcp"], default="stdio", help="Transport mode")
    parser.add_argument("--host", default="127.0.0.1", help="TCP bind host")
    parser.add_argument("--port", type=int, default=8765, help="TCP bind port")
    parser.add_argument("--corpus-root", type=Path, default=runtime_paths.corpus_root, help="Path to the Dory corpus")
    parser.add_argument("--index-root", type=Path, default=runtime_paths.index_root, help="Path to the Dory index")
    args = parser.parse_args(argv)
    return McpServeConfig(
        mode=args.mode,
        host=args.host,
        port=args.port,
        corpus_root=args.corpus_root,
        index_root=args.index_root,
    )


def main(argv: list[str] | None = None) -> None:
    config = parse_serve_args(argv)
    try:
        settings = DorySettings()
        core = RuntimeCore(
            corpus_root=config.corpus_root,
            index_root=config.index_root,
            embedder=build_runtime_embedder(),
            query_expander=_build_query_expander(settings),
            retrieval_planner=_build_retrieval_planner(settings, purpose="query"),
            reranker=build_reranker(settings),
            rerank_candidate_limit=settings.query_reranker_candidate_limit,
        )
    except EmbeddingConfigurationError as err:
        raise SystemExit(str(err)) from err
    if config.mode == "tcp":
        serve_tcp(core=core, host=config.host, port=config.port)
        return
    serve_stdio(core=core)


def _render_result(result: Any) -> str:
    if hasattr(result, "model_dump_json"):
        return result.model_dump_json(indent=2)
    if isinstance(result, (dict, list)):
        return json.dumps(result, indent=2, sort_keys=True)
    return str(result)


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


def _build_query_expander(settings) -> OpenRouterQueryExpander | None:
    if not settings.query_expansion_enabled or settings.query_expansion_max <= 0:
        return None
    client = build_openrouter_client(settings, purpose="query")
    if client is None:
        return None
    return OpenRouterQueryExpander(client=client, max_expansions=settings.query_expansion_max)


def _build_retrieval_planner(settings: DorySettings, *, purpose: str) -> OpenRouterRetrievalPlanner | None:
    client = build_openrouter_client(settings, purpose=purpose)
    if client is None:
        return None
    return OpenRouterRetrievalPlanner(client=client)


if __name__ == "__main__":
    main()
