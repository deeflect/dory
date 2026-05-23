from __future__ import annotations

from typing import Any, Protocol


class _SupportsRequest(Protocol):
    """Protocol for objects that support HTTP-like request calls."""
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class DoryProviderError(RuntimeError):
    """Structured error for Dory provider request failures."""

    def __init__(self, message: str, *, status_code: int | None = None, error_type: str = "error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type


# ── response error helpers ────────────────────────────────────────────────

def _safe_response_text(response: Any) -> str:
    text = str(getattr(response, "text", "") or "").strip()
    return text if text else f"dory request failed: {getattr(response, 'status_code', 'unknown')}"


def _error_type_for_status(status_code: int) -> str:
    if status_code == 404:
        return "not_found"
    if status_code in {400, 422}:
        return "validation_error"
    if status_code in {401, 403}:
        return "permission_denied"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "server_error"
    return "http_error"


def _response_error_message(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        return _safe_response_text(response)
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(error, str) and error.strip():
            return error.strip()
        # Legacy / wiki responses still emit FastAPI's default `detail` field.
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, dict):
            message = detail.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return _safe_response_text(response)
