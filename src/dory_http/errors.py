from __future__ import annotations

import contextvars
from contextvars import Token
from typing import Any, NoReturn

from fastapi import HTTPException

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "dory_request_id",
    default=None,
)


def current_request_id() -> str | None:
    return _request_id_var.get()


def set_request_id(request_id: str) -> Token[str | None]:
    return _request_id_var.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id_var.reset(token)


_HTTP_STATUS_CODE_TO_API_CODE = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    503: "service_unavailable",
}


def raise_api_error(
    *,
    status_code: int,
    code: str,
    message: str,
    error_type: str,
    cause: Exception,
) -> NoReturn:
    detail: dict[str, str] = {
        "code": code,
        "message": message,
        "type": error_type,
    }
    request_id = current_request_id()
    if request_id is not None:
        detail["request_id"] = request_id
    raise HTTPException(status_code=status_code, detail=detail) from cause


def api_error_payload(
    *,
    status_code: int,
    detail: object,
) -> dict[str, dict[str, Any]]:
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        error: dict[str, Any] = {
            "code": str(detail.get("code")),
            "message": str(detail.get("message")),
            "type": str(detail.get("type", "http_error")),
        }
        for key, value in detail.items():
            if key in error:
                continue
            error[key] = value
    else:
        error = {
            "code": _HTTP_STATUS_CODE_TO_API_CODE.get(status_code, f"http_{status_code}"),
            "message": str(detail) if detail is not None else "",
            "type": "http_error",
        }
    request_id = current_request_id()
    if request_id is not None and "request_id" not in error:
        error["request_id"] = request_id
    return {"error": error}


def raise_http_error_for_runtime_value_error(err: ValueError) -> NoReturn:
    message = str(err)
    status_code = 404 if message.startswith("path not found:") else 400
    raise HTTPException(status_code=status_code, detail=message) from err
