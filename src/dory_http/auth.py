from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets
from pathlib import Path
import time

from fastapi import HTTPException, Request, status

from dory_core.config import DorySettings
from dory_core.fs import atomic_write_text


WEB_AUTH_COOKIE = "dory_token"
WEB_SESSION_COOKIE = "dory_web_session"
WEB_PASSWORD_ENV = "DORY_WEB_PASSWORD"
NO_AUTH_DETAIL = "authentication is required; add a bearer token or set DORY_ALLOW_NO_AUTH=true for local development"
_WEB_SESSION_TTL_SECONDS = 60 * 60 * 24 * 30


@dataclass(frozen=True, slots=True)
class WebLoginResult:
    session_cookie: str


def issue_token(name: str, path: Path) -> str:
    token = f"dory_{secrets.token_urlsafe(24)}"
    tokens = load_tokens(path)
    tokens[name] = token
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(tokens, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return token


def load_tokens(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw_payload = path.read_text(encoding="utf-8").strip()
    if not raw_payload:
        return {}
    payload = json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid auth token payload in {path}")
    return {str(key): str(value) for key, value in payload.items()}


def authorize_request(
    request: Request,
    auth_tokens_path: Path | None = None,
    *,
    allow_no_auth: bool = False,
) -> None:
    if allow_no_auth:
        return
    if auth_tokens_path is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=NO_AUTH_DETAIL)

    tokens = _load_tokens_or_raise(auth_tokens_path)
    if not tokens:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=NO_AUTH_DETAIL)

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )

    token = header.removeprefix("Bearer ").strip()
    if not _token_matches(token, tokens):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
        )


def authorize_web_request(
    request: Request,
    auth_tokens_path: Path | None = None,
    *,
    allow_no_auth: bool = False,
) -> str | None:
    """Authorize browser routes, returning a query token that should be persisted as a cookie."""
    if allow_no_auth:
        return None

    web_password = _web_password()
    if web_password and _web_session_is_valid(
        request.cookies.get(WEB_SESSION_COOKIE, ""),
        web_password=web_password,
    ):
        return None

    if auth_tokens_path is None and not web_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=NO_AUTH_DETAIL)

    tokens: dict[str, str] = {}
    if auth_tokens_path is not None:
        tokens = _load_tokens_or_raise(auth_tokens_path)
    if not tokens and not web_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=NO_AUTH_DETAIL)
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="open /wiki/login to authorize this browser",
        )

    legacy_authorized, legacy_token = _authorized_bearer_or_query_token(request, tokens)
    if legacy_authorized:
        return legacy_token

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="open /wiki/login to authorize this browser")


def login_web_password(password: str) -> WebLoginResult:
    web_password = _web_password()
    if not web_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{WEB_PASSWORD_ENV} is not configured",
        )
    if not secrets.compare_digest(password, web_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid wiki password",
        )
    return WebLoginResult(session_cookie=_issue_web_session(web_password=web_password))


def _authorized_bearer_or_query_token(request: Request, tokens: dict[str, str]) -> tuple[bool, str | None]:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header.removeprefix("Bearer ").strip()
        if _token_matches(token, tokens):
            return True, None

    query_token = request.query_params.get("token", "").strip()
    if query_token and _token_matches(query_token, tokens):
        return True, query_token

    cookie_token = request.cookies.get(WEB_AUTH_COOKIE, "").strip()
    if cookie_token and _token_matches(cookie_token, tokens):
        return True, None

    return False, None


def _load_tokens_or_raise(path: Path) -> dict[str, str]:
    try:
        return load_tokens(path)
    except (OSError, json.JSONDecodeError, ValueError) as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"invalid auth token configuration: {path}",
        ) from err


def _token_matches(token: str, tokens: dict[str, str]) -> bool:
    if not token:
        return False
    return any(secrets.compare_digest(token, candidate) for candidate in tokens.values())


def _web_password() -> str:
    return (DorySettings().web_password or "").strip()


def _issue_web_session(*, web_password: str) -> str:
    issued_at = str(int(time.time()))
    signature = _sign_web_session(issued_at, web_password=web_password)
    payload = f"{issued_at}:{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _web_session_is_valid(cookie_value: str, *, web_password: str) -> bool:
    if not cookie_value:
        return False
    try:
        padded = cookie_value + ("=" * (-len(cookie_value) % 4))
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        issued_at, signature = decoded.split(":", 1)
        issued_timestamp = int(issued_at)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    if issued_timestamp < int(time.time()) - _WEB_SESSION_TTL_SECONDS:
        return False
    expected = _sign_web_session(issued_at, web_password=web_password)
    return hmac.compare_digest(signature, expected)


def _sign_web_session(issued_at: str, *, web_password: str) -> str:
    secret = hashlib.sha256(f"dory-web-session:{web_password}".encode("utf-8")).digest()
    return hmac.new(secret, issued_at.encode("utf-8"), hashlib.sha256).hexdigest()
