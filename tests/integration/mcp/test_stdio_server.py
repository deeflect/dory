from __future__ import annotations

import json
from dataclasses import dataclass
from io import StringIO

from dory_mcp.server import serve_stdio


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


def test_stdio_server_lists_tools_and_calls_wake() -> None:
    stdin = StringIO(
        "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "dory_wake",
                            "arguments": {"agent": "codex", "budget_tokens": 600},
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    stdout = StringIO()

    serve_stdio(FakeCore(), stdin=stdin, stdout=stdout)

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert responses[0]["result"]["tools"][0]["name"] == "dory_wake"
    assert responses[1]["result"]["content"][0]["type"] == "text"
    assert '"verb": "wake"' in responses[1]["result"]["content"][0]["text"]


def test_stdio_server_calls_semantic_memory_write() -> None:
    stdin = StringIO(
        "\n".join(
            [
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
            ]
        )
        + "\n"
    )
    stdout = StringIO()

    serve_stdio(FakeCore(), stdin=stdin, stdout=stdout)

    response = json.loads(stdout.getvalue().strip())
    assert response["result"]["content"][0]["type"] == "text"
    assert '"verb": "memory_write"' in response["result"]["content"][0]["text"]


def test_stdio_server_calls_research() -> None:
    stdin = StringIO(
        "\n".join(
            [
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
            ]
        )
        + "\n"
    )
    stdout = StringIO()

    serve_stdio(FakeCore(), stdin=stdin, stdout=stdout)

    response = json.loads(stdout.getvalue().strip())
    assert response["result"]["content"][0]["type"] == "text"
    assert '"verb": "research"' in response["result"]["content"][0]["text"]


def test_stdio_server_returns_error_and_continues_after_unknown_tool() -> None:
    stdin = StringIO(
        "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "unknown_tool",
                            "arguments": {},
                        },
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "dory_wake",
                            "arguments": {"agent": "codex", "budget_tokens": 600},
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    stdout = StringIO()

    serve_stdio(FakeCore(), stdin=stdin, stdout=stdout)

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert responses[0]["error"]["code"] == -32601
    assert responses[0]["error"]["message"] == "unknown tool: unknown_tool"
    assert responses[1]["result"]["content"][0]["type"] == "text"
    assert '"verb": "wake"' in responses[1]["result"]["content"][0]["text"]


def test_stdio_server_returns_parse_error_and_continues_after_bad_json() -> None:
    stdin = StringIO(
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
    )
    stdout = StringIO()

    serve_stdio(FakeCore(), stdin=stdin, stdout=stdout)

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] is None
    assert responses[1]["result"]["content"][0]["type"] == "text"
    assert '"verb": "wake"' in responses[1]["result"]["content"][0]["text"]


def test_stdio_server_does_not_respond_to_initialized_notification() -> None:
    stdin = StringIO(
        json.dumps({"jsonrpc": "2.0", "method": "initialized"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        + "\n"
    )
    stdout = StringIO()

    serve_stdio(FakeCore(), stdin=stdin, stdout=stdout)

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(responses) == 1
    assert responses[0]["result"]["tools"][0]["name"] == "dory_wake"
