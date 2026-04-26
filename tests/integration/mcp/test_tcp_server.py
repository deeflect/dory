from __future__ import annotations

import json
import socket
import threading
from dataclasses import dataclass
from pathlib import Path

from dory_mcp.auth import TcpAuthConfig, load_tcp_auth_config
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
    server = build_tcp_server(FakeCore(), host="127.0.0.1", port=0, allow_no_auth=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        with socket.create_connection((host, port), timeout=2.0) as connection:
            connection.sendall(
                (
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                    + "\n"
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
    assert '"verb":"wake"' in responses[1]["result"]["content"][0]["text"]


def test_tcp_server_calls_semantic_memory_write() -> None:
    server = build_tcp_server(FakeCore(), host="127.0.0.1", port=0, allow_no_auth=True)
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
                                    "subject": "avery",
                                    "content": "Avery prefers async work.",
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
    assert '"verb":"memory_write"' in response["result"]["content"][0]["text"]


def test_tcp_server_calls_research() -> None:
    server = build_tcp_server(FakeCore(), host="127.0.0.1", port=0, allow_no_auth=True)
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
    assert '"verb":"research"' in response["result"]["content"][0]["text"]


def test_tcp_server_returns_parse_error_and_continues_after_bad_json() -> None:
    server = build_tcp_server(FakeCore(), host="127.0.0.1", port=0, allow_no_auth=True)
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
    assert '"verb":"wake"' in responses[1]["result"]["content"][0]["text"]


def _start_server(auth_config: TcpAuthConfig):
    server = build_tcp_server(FakeCore(), host="127.0.0.1", port=0, auth_config=auth_config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _send_lines(host: str, port: int, lines: list[str]) -> list[dict]:
    with socket.create_connection((host, port), timeout=2.0) as connection:
        connection.sendall(("\n".join(lines) + "\n").encode("utf-8"))
        connection.shutdown(socket.SHUT_WR)
        stream = connection.makefile("r", encoding="utf-8")
        return [json.loads(line) for line in stream if line.strip()]


def test_tcp_server_rejects_requests_without_bearer_token() -> None:
    auth_config = TcpAuthConfig(tokens=("dory_secret",), allow_no_auth=False)
    server, thread = _start_server(auth_config)
    try:
        host, port = server.server_address
        responses = _send_lines(
            host,
            port,
            [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert responses[0]["error"]["code"] == -32001
    assert "bearer token" in responses[0]["error"]["message"]


def test_tcp_server_rejects_invalid_bearer_token() -> None:
    auth_config = TcpAuthConfig(tokens=("dory_secret",), allow_no_auth=False)
    server, thread = _start_server(auth_config)
    try:
        host, port = server.server_address
        responses = _send_lines(
            host,
            port,
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/list",
                        "params": {"_auth": {"token": "wrong-token"}},
                    }
                )
            ],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert responses[0]["error"]["code"] == -32001
    assert "invalid" in responses[0]["error"]["message"]


def test_tcp_server_authorizes_connection_after_first_valid_token() -> None:
    auth_config = TcpAuthConfig(tokens=("dory_secret",), allow_no_auth=False)
    server, thread = _start_server(auth_config)
    try:
        host, port = server.server_address
        # Same TCP connection: first request carries the token, second omits it.
        first = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_auth": {"token": "dory_secret"}},
            }
        )
        second = json.dumps(
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
        responses = _send_lines(host, port, [first, second])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert "tools" in responses[0]["result"]
    assert '"verb":"wake"' in responses[1]["result"]["content"][0]["text"]


def test_tcp_server_strips_auth_field_before_dispatch() -> None:
    auth_config = TcpAuthConfig(tokens=("dory_secret",), allow_no_auth=False)
    server, thread = _start_server(auth_config)
    try:
        host, port = server.server_address
        responses = _send_lines(
            host,
            port,
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "dory_wake",
                            "arguments": {"agent": "codex", "budget_tokens": 600},
                            "_auth": {"token": "dory_secret"},
                        },
                    }
                )
            ],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    body = responses[0]["result"]["content"][0]["text"]
    # _auth must not leak into the handler's view of params.
    assert "_auth" not in body
    assert '"verb":"wake"' in body


def test_load_tcp_auth_config_refuses_missing_tokens(tmp_path: Path) -> None:
    import pytest

    missing = tmp_path / "absent.json"
    with pytest.raises(ValueError, match="auth tokens"):
        load_tcp_auth_config(auth_tokens_path=missing, allow_no_auth=False)


def test_load_tcp_auth_config_loads_tokens_from_file(tmp_path: Path) -> None:
    tokens_path = tmp_path / "auth-tokens.json"
    tokens_path.write_text('{"primary": "dory_xyz"}', encoding="utf-8")

    config = load_tcp_auth_config(auth_tokens_path=tokens_path, allow_no_auth=False)

    assert config.tokens == ("dory_xyz",)
    assert config.required is True


def test_load_tcp_auth_config_allow_no_auth_returns_empty_config(tmp_path: Path) -> None:
    config = load_tcp_auth_config(auth_tokens_path=None, allow_no_auth=True)

    assert config.tokens == ()
    assert config.required is False
