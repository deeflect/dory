"""Bearer-token authentication for the MCP TCP transport.

The TCP server reuses the same `auth-tokens.json` file as the HTTP daemon so a
single token rotation covers both surfaces. Stdio MCP intentionally has no auth
because the process is launched by, and lives inside, the trust boundary of the
calling agent.

Wire-format: clients pass the token as ``params._auth.token`` on the *first*
request on a TCP connection (typically ``initialize``). Once accepted, the
connection is marked authorized for its lifetime and subsequent requests do not
need to repeat the token.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TcpAuthConfig:
    """Resolved bearer-auth policy for one MCP TCP server instance."""

    tokens: tuple[str, ...]
    allow_no_auth: bool

    @property
    def required(self) -> bool:
        return not self.allow_no_auth


def load_tcp_auth_config(
    *,
    auth_tokens_path: Path | None,
    allow_no_auth: bool,
) -> TcpAuthConfig:
    """Build a :class:`TcpAuthConfig` from a tokens file and an opt-out flag."""
    if allow_no_auth:
        return TcpAuthConfig(tokens=(), allow_no_auth=True)
    if auth_tokens_path is None:
        raise ValueError(
            "MCP TCP requires either --auth-tokens-path / DORY_AUTH_TOKENS_PATH "
            "or DORY_ALLOW_NO_AUTH=true; refusing to expose the listener with no auth."
        )
    tokens = _load_tokens(auth_tokens_path)
    if not tokens:
        raise ValueError(
            f"MCP TCP auth tokens file is empty: {auth_tokens_path}. "
            "Run `dory auth new <name>` to issue a token, or set DORY_ALLOW_NO_AUTH=true."
        )
    return TcpAuthConfig(tokens=tokens, allow_no_auth=False)


def extract_and_strip_auth(request: dict[str, Any]) -> str | None:
    """Pop ``params._auth.token`` from a JSON-RPC request and return it.

    Mutates ``request`` in place to remove the ``_auth`` key so downstream
    handlers don't see transport-level auth fields. Returns ``None`` if no
    token was provided.
    """
    params = request.get("params")
    if not isinstance(params, dict):
        return None
    auth = params.pop("_auth", None)
    if not isinstance(auth, dict):
        return None
    token = auth.get("token")
    return str(token) if isinstance(token, str) and token else None


def token_matches(token: str | None, config: TcpAuthConfig) -> bool:
    if not token:
        return False
    return any(secrets.compare_digest(token, candidate) for candidate in config.tokens)


def auth_error_response(request_id: Any, *, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32001, "message": message},
    }


def _load_tokens(path: Path) -> tuple[str, ...]:
    if not path.exists():
        return ()
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as err:
        raise ValueError(f"unable to read MCP auth tokens file: {path}") from err
    if not raw:
        return ()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        raise ValueError(f"invalid JSON in MCP auth tokens file: {path}") from err
    if not isinstance(payload, dict):
        raise ValueError(f"MCP auth tokens file must be a JSON object: {path}")
    return tuple(str(value) for value in payload.values() if value)
