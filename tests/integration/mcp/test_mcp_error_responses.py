from __future__ import annotations

from dataclasses import dataclass

from dory_mcp.server import DoryMcpServer


@dataclass
class _ExplodingCore:
    def wake(self, req):
        return {"verb": "wake", "request": req}

    def active_memory(self, req):
        raise AssertionError(f"unexpected call: {req}")

    def search(self, req):
        raise ValueError("bad search args")

    def get(self, req):
        raise AssertionError(f"unexpected call: {req}")

    def memory_write(self, req):
        raise AssertionError(f"unexpected call: {req}")

    def write(self, req):
        raise AssertionError(f"unexpected call: {req}")

    def research(self, req):
        raise AssertionError(f"unexpected call: {req}")

    def link(self, req):
        raise AssertionError(f"unexpected call: {req}")

    def status(self, req):
        raise AssertionError(f"unexpected call: {req}")


def test_mcp_unknown_tool_returns_jsonrpc_error() -> None:
    server = DoryMcpServer(core=_ExplodingCore())

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        }
    )

    assert response["error"]["code"] == -32601
    assert response["error"]["message"] == "unknown tool: nope"


def test_mcp_handler_validation_error_returns_jsonrpc_error() -> None:
    server = DoryMcpServer(core=_ExplodingCore())

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "dory_search", "arguments": {"query": "anna"}},
        }
    )

    assert response["error"]["code"] == -32602
    assert response["error"]["message"] == "bad search args"


def test_mcp_initialize_returns_capabilities_and_initialized_is_notification() -> None:
    server = DoryMcpServer(core=_ExplodingCore())

    initialized = server.handle({"jsonrpc": "2.0", "method": "initialized"})
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert initialized is None
    assert response is not None
    assert response["result"]["protocolVersion"] == "2024-11-05"
    assert response["result"]["serverInfo"]["name"] == "dory"
    assert response["result"]["capabilities"] == {"tools": {}}
