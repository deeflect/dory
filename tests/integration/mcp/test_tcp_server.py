from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass

from dory_mcp.server import build_tcp_server


@dataclass
class FakeCore:
    def wake(self, req):
        return {"verb": "wake", "request": req}

    def search(self, req):
        return {"verb": "search", "request": req}

    def get(self, req):
        return {"verb": "get", "request": req}

    def memory_write(self, req):
        return {"verb": "memory_write", "request": req}

    def write(self, req):
        return {"verb": "write", "request": req}

    def link(self, req):
        return {"verb": "link", "request": req}

    def research(self, req):
        return {"verb": "research", "request": req}


def test_tcp_server_lists_tools_and_calls_wake() -> None:
    server = build_tcp_server(FakeCore(), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with socket.create_connection((host, port), timeout=2.0) as connection:
            connection.sendall(
                (
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
                    + json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": "dory_wake",
                                "arguments": {"agent": "codex", "budget_tokens": 600},
                            },
                        }
                    )
                    + "\n"
                ).encode("utf-8")
            )
            connection.shutdown(socket.SHUT_WR)
            stream = connection.makefile("r", encoding="utf-8")
            responses = [json.loads(stream.readline()) for _ in range(2)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert responses[0]["result"]["tools"][0]["name"] == "dory_wake"
    assert '"verb": "wake"' in responses[1]["result"]["content"][0]["text"]


def test_tcp_server_calls_semantic_memory_write() -> None:
    server = build_tcp_server(FakeCore(), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with socket.create_connection((host, port), timeout=2.0) as connection:
            connection.sendall(
                (
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {
                                "name": "dory_memory_write",
                                "arguments": {
                                    "action": "write",
                                    "kind": "fact",
                                    "subject": "anna",
                                    "content": "Anna prefers async work.",
                                },
                            },
                        }
                    )
                    + "\n"
                ).encode("utf-8")
            )
            connection.shutdown(socket.SHUT_WR)
            stream = connection.makefile("r", encoding="utf-8")
            response = json.loads(stream.readline())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert response["result"]["content"][0]["type"] == "text"
    assert '"verb": "memory_write"' in response["result"]["content"][0]["text"]


def test_tcp_server_calls_research() -> None:
    server = build_tcp_server(FakeCore(), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with socket.create_connection((host, port), timeout=2.0) as connection:
            connection.sendall(
                (
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {
                                "name": "dory_research",
                                "arguments": {
                                    "question": "What are we working on right now?",
                                    "kind": "report",
                                    "corpus": "all",
                                    "limit": 3,
                                    "save": True,
                                },
                            },
                        }
                    )
                    + "\n"
                ).encode("utf-8")
            )
            connection.shutdown(socket.SHUT_WR)
            stream = connection.makefile("r", encoding="utf-8")
            response = json.loads(stream.readline())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert response["result"]["content"][0]["type"] == "text"
    assert '"verb": "research"' in response["result"]["content"][0]["text"]


def test_tcp_server_returns_parse_error_and_continues_after_bad_json() -> None:
    server = build_tcp_server(FakeCore(), host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with socket.create_connection((host, port), timeout=2.0) as connection:
            connection.sendall(
                (
                    "not-json\n"
                    + json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": "dory_wake",
                                "arguments": {"agent": "codex", "budget_tokens": 600},
                            },
                        }
                    )
                    + "\n"
                ).encode("utf-8")
            )
            connection.shutdown(socket.SHUT_WR)
            stream = connection.makefile("r", encoding="utf-8")
            responses = [json.loads(stream.readline()) for _ in range(2)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] is None
    assert '"verb": "wake"' in responses[1]["result"]["content"][0]["text"]
