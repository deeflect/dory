#!/usr/bin/env python3
"""
Thin MCP stdio bridge that proxies to a running dory-http server.
Claude Code spawns this as a subprocess; it speaks JSON-RPC on stdin/stdout
and forwards tool calls to the Dory HTTP API.

Tool schemas come from the server at /v1/tools (the canonical source of truth
lives in `dory_core.tool_registry`). The only bridge-local behavior is:
  - pre-wake session sync (fired before dory_wake)
  - a small set of client-side defaults (agent="claude-code", etc.)
  - GET routing for dory_get / dory_status

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

# Minimal offline fallback: only surfaces dory_status / dory_search if /v1/tools
# is unreachable at tools/list time. Full schemas come from the server.
_FALLBACK_TOOLS: list[dict[str, Any]] = [
    {
        "name": "dory_status",
        "description": "Get Dory index status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "dory_search",
        "description": "Hybrid search over Dory memory corpus.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]

# Tools served over HTTP GET. Everything else is POST with JSON body.
_GET_TOOLS: dict[str, str] = {
    "dory_get": "/v1/get",
    "dory_status": "/v1/status",
}

# Bridge-side default injection. These are client-context defaults that don't
# belong in the server's Pydantic models.
_CLIENT_DEFAULTS: dict[str, dict[str, Any]] = {
    "dory_wake": {"agent": "claude-code", "profile": "coding", "include_recent_sessions": 0},
    "dory_active_memory": {"agent": "claude-code"},
    "dory_write": {"agent": "claude-code"},
    "dory_memory_write": {"agent": "claude-code"},
}


def _endpoint_for(tool_name: str) -> str:
    """Map `dory_memory_write` → `/v1/memory-write`. Matches dory_http routes."""
    stem = tool_name.removeprefix("dory_").replace("_", "-")
    return f"/v1/{stem}"


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
    if isinstance(tools, list) and tools:
        return [_decorate_schema(tool) for tool in tools]
    return _FALLBACK_TOOLS


def _decorate_schema(tool: dict[str, Any]) -> dict[str, Any]:
    """Patch client-side defaults into the advertised schema so agents see them."""
    name = tool.get("name", "")
    overrides = _CLIENT_DEFAULTS.get(name)
    if not overrides:
        return tool
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict):
        return tool
    props = schema.get("properties")
    if not isinstance(props, dict):
        return tool
    patched_props = dict(props)
    for field_name, default_value in overrides.items():
        existing = patched_props.get(field_name)
        if isinstance(existing, dict):
            patched = dict(existing)
            patched["default"] = default_value
            patched_props[field_name] = patched
    return {**tool, "inputSchema": {**schema, "properties": patched_props}}


def handle_tool_call(name: str, args: dict[str, Any]) -> str:
    # Pre-wake session sync stays here — it's a local-client side effect, not
    # something the server can do for us.
    pre_call_result: dict[str, Any] | None = None
    if name == "dory_wake":
        pre_call_result = sync_sessions_before_wake()

    merged_args = {**_CLIENT_DEFAULTS.get(name, {}), **args}

    if name in _GET_TOOLS:
        endpoint = _GET_TOOLS[name]
        query_params = _get_query_params(name, merged_args)
        url = f"{endpoint}?{urllib.parse.urlencode(query_params)}" if query_params else endpoint
        result = http_get(url)
    else:
        result = http_post(_endpoint_for(name), merged_args)

    if not isinstance(result, dict):
        result = {"ok": False, "error": {"type": "protocol_error", "message": "non-dict response"}}
    if pre_call_result is not None:
        result["session_sync"] = pre_call_result
    return json.dumps(result, indent=2)


def _get_query_params(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "dory_get":
        params: dict[str, Any] = {}
        if "path" in args:
            params["path"] = args["path"]
        # `from_line` was an older alias for `from`; keep accepting it.
        from_value = args.get("from", args.get("from_line"))
        if from_value is not None:
            params["from"] = from_value
        if "lines" in args:
            params["lines"] = args["lines"]
        return params
    return {}


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
                        "serverInfo": {"name": "dory-http-bridge", "version": "0.3.0"},
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
