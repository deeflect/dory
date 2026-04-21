#!/usr/bin/env python3
"""
Thin MCP stdio bridge that proxies to a running dory-http server.
Claude Code spawns this as a subprocess; it speaks JSON-RPC on stdin/stdout
and forwards tool calls to the Dory HTTP API.

Usage in Claude Code MCP config:
  "command": "python3",
  "args": ["/path/to/dory-mcp-http-bridge.py"],
  "env": { "DORY_HTTP_URL": "http://127.0.0.1:8766" }
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DORY_URL = os.environ.get("DORY_HTTP_URL", "http://127.0.0.1:8766").rstrip("/")
DORY_TOKEN = (os.environ.get("DORY_HTTP_TOKEN") or os.environ.get("DORY_CLIENT_AUTH_TOKEN") or "").strip()
REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_ENV_PATH = Path(os.environ.get("DORY_CLIENT_ENV_FILE", str(Path.home() / ".config" / "dory" / "client.env")))
DEFAULT_SPOOL_ROOT = Path.home() / ".local" / "share" / "dory" / "spool"
DEFAULT_HARNESSES = "claude codex opencode openclaw hermes"

TOOLS = [
    {
        "name": "dory_wake",
        "description": (
            "Get the frozen wake-up context block. Call this at session start or task switch. "
            "Use search/get for active project or writing-specific context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "budget_tokens": {"type": "integer", "default": 1200},
                "agent": {"type": "string", "default": "claude-code"},
                "profile": {
                    "type": "string",
                    "default": "coding",
                    "enum": ["default", "casual", "coding", "writing", "privacy"],
                },
                "include_recent_sessions": {"type": "integer", "default": 0},
                "include_pinned_decisions": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "dory_search",
        "description": "Hybrid search over Dory memory corpus.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 5},
                "mode": {
                    "type": "string",
                    "default": "hybrid",
                    "enum": ["bm25", "text", "keyword", "lexical", "vector", "semantic", "hybrid", "recall", "exact"],
                },
                "corpus": {"type": "string", "default": "durable", "enum": ["durable", "sessions", "all"]},
                "scope": {
                    "type": "object",
                    "properties": {
                        "path_glob": {"type": "string"},
                        "type": {"type": "array", "items": {"type": "string"}},
                        "status": {"type": "array", "items": {"type": "string"}},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "since": {"type": "string"},
                        "until": {"type": "string"},
                    },
                },
                "include_content": {"type": "boolean"},
                "min_score": {"type": "number"},
                "rerank": {"type": "string", "default": "auto", "enum": ["auto", "true", "false"]},
                "debug": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "dory_research",
        "description": (
            "Run Dory research mode for bounded, citable multi-source investigations. "
            "Use only when search/get would require several source files."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "kind": {
                    "type": "string",
                    "default": "report",
                    "enum": ["report", "briefing", "wiki-note", "proposal"],
                },
                "corpus": {"type": "string", "default": "all", "enum": ["durable", "sessions", "all"]},
                "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 20},
                "save": {"type": "boolean", "default": True},
            },
            "required": ["question"],
        },
    },
    {
        "name": "dory_active_memory",
        "description": (
            "Run the staged active-memory pass before replying. Use for high-stakes or ambiguous "
            "answers, not as a default replacement for wake/search/get. Limits: budget_tokens <= 1200, "
            "timeout_ms <= 5000. Set include_wake=false when wake was already called."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "agent": {"type": "string", "default": "claude-code"},
                "cwd": {"type": "string"},
                "profile": {
                    "type": "string",
                    "default": "auto",
                    "enum": ["auto", "general", "coding", "writing", "privacy", "personal"],
                },
                "timeout_ms": {"type": "integer", "default": 1200, "minimum": 100, "maximum": 5000},
                "budget_tokens": {"type": "integer", "default": 400, "minimum": 100, "maximum": 1200},
                "include_wake": {"type": "boolean", "default": True},
                "rerank": {"type": "string", "default": "auto", "enum": ["auto", "true", "false"]},
            },
            "required": ["prompt", "agent"],
        },
    },
    {
        "name": "dory_get",
        "description": "Fetch a specific file from the corpus by path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "from": {"type": "integer", "default": 1},
                "from_line": {"type": "integer", "default": 1},
                "lines": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "dory_link",
        "description": "Inspect backlinks, neighbors, or run link lint.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["neighbors", "backlinks", "lint"]},
                "path": {"type": "string"},
                "direction": {"type": "string", "default": "out", "enum": ["out", "in", "both"]},
                "depth": {"type": "integer", "default": 1},
            },
            "required": ["op"],
        },
    },
    {
        "name": "dory_memory_write",
        "description": (
            "Persist semantic memory through Dory. Prefer this over dory_write for new "
            "remember/save/update/forget actions. Semantic subjects can route into canonical docs; "
            "set dry_run=true to preview, allow_canonical=true to commit canonical writes, or "
            "force_inbox=true for tentative/scratch captures."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["write", "replace", "forget"],
                },
                "kind": {
                    "type": "string",
                    "enum": ["fact", "preference", "state", "decision", "note"],
                },
                "subject": {"type": "string"},
                "content": {"type": "string"},
                "scope": {"type": "string", "enum": ["person", "project", "concept", "decision", "core"]},
                "confidence": {"type": "string"},
                "source": {"type": "string"},
                "soft": {"type": "boolean", "default": False},
                "dry_run": {"type": "boolean", "default": False},
                "force_inbox": {"type": "boolean", "default": False},
                "allow_canonical": {"type": "boolean", "default": False},
                "agent": {"type": "string", "default": "claude-code"},
                "session_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["action", "kind", "subject", "content"],
        },
    },
    {
        "name": "dory_write",
        "description": (
            "Exact-path markdown write through Dory. Use only when you know the target "
            "path and, for replace/forget, have read the current hash first. Prefer dory_memory_write "
            "for semantic remember/save/update actions. Set dry_run=true to validate and preview without writing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["append", "create", "replace", "forget"]},
                "target": {"type": "string"},
                "content": {"type": "string"},
                "soft": {"type": "boolean", "default": False},
                "dry_run": {"type": "boolean", "default": False},
                "frontmatter": {"type": "object"},
                "agent": {"type": "string", "default": "claude-code"},
                "session_id": {"type": "string"},
                "expected_hash": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["kind", "target"],
        },
    },
    {
        "name": "dory_purge",
        "description": (
            "Hard-delete an exact markdown path from the corpus and index. Defaults to dry_run=true. "
            "Live purge requires reason and matching expected_hash. Only scratch/generated roots are "
            "allowed unless allow_canonical=true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "expected_hash": {"type": "string"},
                "reason": {"type": "string"},
                "dry_run": {"type": "boolean", "default": True},
                "allow_canonical": {"type": "boolean", "default": False},
                "include_related_tombstone": {"type": "boolean", "default": False},
            },
            "required": ["target"],
        },
    },
    {
        "name": "dory_status",
        "description": "Get Dory index status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def http_post(endpoint: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{DORY_URL}{endpoint}"
    headers = {"Content-Type": "application/json"}
    if DORY_TOKEN:
        headers["Authorization"] = f"Bearer {DORY_TOKEN}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers)
    return _perform_request(request)


def http_get(endpoint: str) -> dict[str, Any]:
    url = f"{DORY_URL}{endpoint}"
    headers: dict[str, str] = {}
    if DORY_TOKEN:
        headers["Authorization"] = f"Bearer {DORY_TOKEN}"
    request = urllib.request.Request(url, headers=headers)
    return _perform_request(request)


def _perform_request(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
            return {
                "ok": False,
                "error": {
                    "type": "protocol_error",
                    "message": "Server returned non-object JSON.",
                },
            }
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:1000]
        return {
            "ok": False,
            "error": {
                "type": "http_error",
                "status": err.code,
                "message": detail or err.reason,
            },
        }
    except Exception as err:
        return {
            "ok": False,
            "error": {
                "type": "transport_error",
                "message": str(err),
            },
        }


def list_tools() -> list[dict[str, Any]]:
    payload = http_get("/v1/tools")
    tools = payload.get("tools")
    if isinstance(tools, list):
        return tools
    return TOOLS


def handle_tool_call(name: str, args: dict[str, Any]) -> str:
    if name == "dory_wake":
        session_sync = sync_sessions_before_wake()
        result = http_post(
            "/v1/wake",
            {
                "budget_tokens": args.get("budget_tokens", 600),
                "agent": args.get("agent", "claude-code"),
                "profile": args.get("profile", "coding"),
                "include_recent_sessions": args.get("include_recent_sessions", 0),
                "include_pinned_decisions": args.get("include_pinned_decisions", True),
            },
        )
        if session_sync is not None:
            result["session_sync"] = session_sync
        return json.dumps(result, indent=2)

    if name == "dory_search":
        payload: dict[str, Any] = {
            "query": args["query"],
            "k": args.get("k", 5),
            "mode": args.get("mode", "hybrid"),
            "corpus": args.get("corpus", "durable"),
        }
        if "scope" in args:
            payload["scope"] = args["scope"]
        if "include_content" in args:
            payload["include_content"] = args["include_content"]
        if "min_score" in args:
            payload["min_score"] = args["min_score"]
        if "rerank" in args:
            payload["rerank"] = args["rerank"]
        if "debug" in args:
            payload["debug"] = args["debug"]
        result = http_post("/v1/search", payload)
        return json.dumps(result, indent=2)

    if name == "dory_research":
        payload: dict[str, Any] = {
            "question": args["question"],
            "kind": args.get("kind", "report"),
            "corpus": args.get("corpus", "all"),
            "limit": args.get("limit", 8),
            "save": args.get("save", True),
        }
        result = http_post("/v1/research", payload)
        return json.dumps(result, indent=2)

    if name == "dory_active_memory":
        payload: dict[str, Any] = {
            "prompt": args["prompt"],
            "agent": args.get("agent", "claude-code"),
        }
        if "cwd" in args:
            payload["cwd"] = args["cwd"]
        if "timeout_ms" in args:
            payload["timeout_ms"] = args["timeout_ms"]
        if "budget_tokens" in args:
            payload["budget_tokens"] = args["budget_tokens"]
        if "profile" in args:
            payload["profile"] = args["profile"]
        if "include_wake" in args:
            payload["include_wake"] = args["include_wake"]
        if "rerank" in args:
            payload["rerank"] = args["rerank"]
        result = http_post("/v1/active-memory", payload)
        return json.dumps(result, indent=2)

    if name == "dory_get":
        params: dict[str, Any] = {"path": args["path"]}
        from_value = args.get("from", args.get("from_line"))
        if from_value is not None:
            params["from"] = from_value
        if "lines" in args:
            params["lines"] = args["lines"]
        query_string = urllib.parse.urlencode(params)
        result = http_get(f"/v1/get?{query_string}")
        return json.dumps(result, indent=2)

    if name == "dory_link":
        payload: dict[str, Any] = {"op": args.get("op", "")}
        if "path" in args:
            payload["path"] = args["path"]
        if "direction" in args:
            payload["direction"] = args["direction"]
        if "depth" in args:
            payload["depth"] = args["depth"]
        result = http_post("/v1/link", payload)
        return json.dumps(result, indent=2)

    if name == "dory_write":
        payload: dict[str, Any] = {
            "kind": args["kind"],
            "target": args["target"],
            "content": args.get("content", ""),
            "agent": args.get("agent", "claude-code"),
        }
        if "frontmatter" in args:
            payload["frontmatter"] = args["frontmatter"]
        if "soft" in args:
            payload["soft"] = args["soft"]
        if "dry_run" in args:
            payload["dry_run"] = args["dry_run"]
        if "session_id" in args:
            payload["session_id"] = args["session_id"]
        if "expected_hash" in args:
            payload["expected_hash"] = args["expected_hash"]
        if "reason" in args:
            payload["reason"] = args["reason"]
        result = http_post("/v1/write", payload)
        return json.dumps(result, indent=2)

    if name == "dory_purge":
        payload: dict[str, Any] = {"target": args["target"]}
        if "expected_hash" in args:
            payload["expected_hash"] = args["expected_hash"]
        if "reason" in args:
            payload["reason"] = args["reason"]
        if "dry_run" in args:
            payload["dry_run"] = args["dry_run"]
        if "allow_canonical" in args:
            payload["allow_canonical"] = args["allow_canonical"]
        if "include_related_tombstone" in args:
            payload["include_related_tombstone"] = args["include_related_tombstone"]
        result = http_post("/v1/purge", payload)
        return json.dumps(result, indent=2)

    if name == "dory_memory_write":
        payload: dict[str, Any] = {
            "action": args["action"],
            "kind": args["kind"],
            "subject": args["subject"],
            "content": args["content"],
            "agent": args.get("agent", "claude-code"),
        }
        if "scope" in args:
            payload["scope"] = args["scope"]
        if "confidence" in args:
            payload["confidence"] = args["confidence"]
        if "source" in args:
            payload["source"] = args["source"]
        if "soft" in args:
            payload["soft"] = args["soft"]
        if "dry_run" in args:
            payload["dry_run"] = args["dry_run"]
        if "force_inbox" in args:
            payload["force_inbox"] = args["force_inbox"]
        if "allow_canonical" in args:
            payload["allow_canonical"] = args["allow_canonical"]
        if "session_id" in args:
            payload["session_id"] = args["session_id"]
        if "reason" in args:
            payload["reason"] = args["reason"]
        result = http_post("/v1/memory-write", payload)
        return json.dumps(result, indent=2)

    if name == "dory_status":
        result = http_get("/v1/status")
        return json.dumps(result, indent=2)

    return json.dumps(
        {
            "ok": False,
            "error": {
                "type": "unknown_tool",
                "message": f"unknown tool: {name}",
            },
        },
        indent=2,
    )


def sync_sessions_before_wake() -> dict[str, Any] | None:
    if not _env_bool("DORY_SYNC_SESSIONS_ON_WAKE", default=True):
        return None

    shipper_script = REPO_ROOT / "scripts" / "ops" / "client-session-shipper.py"
    if not shipper_script.exists():
        return {
            "ok": False,
            "error": "client-session-shipper.py not found",
        }

    session_env = _session_sync_env()
    spool_root = session_env.get("DORY_CLIENT_SPOOL_ROOT") or str(DEFAULT_SPOOL_ROOT)
    checkpoints_path = session_env.get("DORY_CLIENT_CHECKPOINTS_PATH") or str(Path(spool_root) / "checkpoints.json")
    harnesses = session_env.get("DORY_CLIENT_HARNESSES") or DEFAULT_HARNESSES
    timeout_seconds = float(session_env.get("DORY_CLIENT_SHIPPER_TIMEOUT_SECONDS") or "3")

    command = [
        sys.executable,
        str(shipper_script),
        "--harnesses",
        harnesses,
        "--spool-root",
        spool_root,
        "--checkpoints-path",
        checkpoints_path,
        "--base-url",
        DORY_URL,
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    if DORY_TOKEN:
        command.extend(["--auth-token", DORY_TOKEN])

    try:
        completed = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds + 2.0,
            env=session_env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "session sync timed out before wake",
        }

    if completed.returncode != 0:
        return {
            "ok": False,
            "error": (completed.stderr or completed.stdout).strip()[:500],
        }

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "session sync returned non-JSON output",
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error": "session sync returned non-object JSON",
        }
    return _compact_session_sync(payload)


def _session_sync_env() -> dict[str, str]:
    loaded = _load_env_file(CLIENT_ENV_PATH)
    merged = {**loaded, **os.environ}
    merged["DORY_HTTP_URL"] = DORY_URL
    if DORY_TOKEN:
        merged["DORY_CLIENT_AUTH_TOKEN"] = DORY_TOKEN
    merged.setdefault("DORY_CLIENT_SPOOL_ROOT", str(DEFAULT_SPOOL_ROOT))
    merged.setdefault("DORY_SESSION_SPOOL_ROOT", merged["DORY_CLIENT_SPOOL_ROOT"])
    merged.setdefault("DORY_CLIENT_CHECKPOINTS_PATH", str(Path(merged["DORY_CLIENT_SPOOL_ROOT"]) / "checkpoints.json"))
    merged.setdefault("DORY_CLIENT_HARNESSES", DEFAULT_HARNESSES)
    return {str(key): str(value) for key, value in merged.items()}


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _parse_shell_value(raw_value)
    return values


def _parse_shell_value(raw_value: str) -> str:
    try:
        parts = shlex.split(f"value={raw_value}", posix=True)
    except ValueError:
        return raw_value.strip().strip("'\"")
    if not parts:
        return ""
    return parts[0].removeprefix("value=")


def _compact_session_sync(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    result_payload = result if isinstance(result, dict) else {}
    captures = payload.get("captures")
    queued = payload.get("queued")
    sent = result_payload.get("sent")
    failed = result_payload.get("failed")
    errors = result_payload.get("errors")
    compact: dict[str, Any] = {
        "ok": not bool(failed),
        "captures": len(captures) if isinstance(captures, list) else 0,
        "queued": len(queued) if isinstance(queued, list) else 0,
        "sent": len(sent) if isinstance(sent, list) else 0,
        "failed": len(failed) if isinstance(failed, list) else 0,
    }
    if isinstance(errors, list) and errors:
        compact["errors"] = [str(item)[:200] for item in errors[:2]]
    return compact


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = message.get("method", "")
        message_id = message.get("id")

        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "dory-http-bridge", "version": "0.2.0"},
                    },
                }
            )
            continue

        if method == "notifications/initialized":
            continue

        if method == "tools/list":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {"tools": list_tools()},
                }
            )
            continue

        if method == "tools/call":
            params = message.get("params") or {}
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            try:
                result_text = handle_tool_call(tool_name, tool_args)
            except Exception as err:
                result_text = json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "type": "bridge_error",
                            "message": str(err),
                        },
                    },
                    indent=2,
                )
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "content": [{"type": "text", "text": result_text}],
                    },
                }
            )
            continue

        if message_id is not None:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )


if __name__ == "__main__":
    main()
