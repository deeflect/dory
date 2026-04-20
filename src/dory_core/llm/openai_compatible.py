from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from dory_core.config import DorySettings
from dory_core.llm.openrouter import OpenRouterProviderError


@dataclass(frozen=True, slots=True)
class OpenAICompatibleJSONClient:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    max_tokens: int = 512
    app_name: str = "dory"
    retries: int = 0

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> Any:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": self.app_name,
        }
        response = self._post_chat_completion(headers=headers, payload=payload)
        try:
            response_payload = response.json()
        except ValueError as err:
            raise OpenRouterProviderError("OpenAI-compatible endpoint returned invalid JSON.") from err

        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterProviderError("OpenAI-compatible endpoint returned no choices.")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise OpenRouterProviderError("OpenAI-compatible endpoint returned a malformed message payload.")
        content_text = _coerce_message_content(message.get("content"))
        try:
            return json.loads(content_text)
        except json.JSONDecodeError as err:
            raise OpenRouterProviderError("OpenAI-compatible endpoint returned non-JSON content.") from err

    def _post_chat_completion(self, *, headers: dict[str, str], payload: dict[str, Any]) -> httpx.Response:
        last_error: Exception | None = None
        with httpx.Client(base_url=_normalize_base_url(self.base_url), timeout=self.timeout_seconds) as client:
            for attempt in range(self.retries + 1):
                try:
                    response = client.post("/chat/completions", headers=headers, json=payload)
                except httpx.HTTPError as err:
                    last_error = err
                    if attempt >= self.retries:
                        raise OpenRouterProviderError(f"OpenAI-compatible request failed: {err}") from err
                    _sleep_backoff(attempt)
                    continue

                if response.status_code < 400:
                    return response
                if not _should_retry_status(response.status_code) or attempt >= self.retries:
                    raise OpenRouterProviderError(_format_error_response(response))
                _sleep_backoff(attempt, retry_after=response.headers.get("Retry-After"))

        if last_error is not None:
            raise OpenRouterProviderError(f"OpenAI-compatible request failed: {last_error}") from last_error
        raise OpenRouterProviderError("OpenAI-compatible request failed: unknown provider error")


def build_local_llm_client(settings: DorySettings | None = None) -> OpenAICompatibleJSONClient | None:
    resolved_settings = settings or DorySettings()
    if not resolved_settings.local_llm_api_key:
        return None
    if not resolved_settings.local_llm_base_url.strip():
        return None
    if not resolved_settings.local_llm_model.strip():
        return None
    return OpenAICompatibleJSONClient(
        api_key=resolved_settings.local_llm_api_key,
        base_url=resolved_settings.local_llm_base_url,
        model=resolved_settings.local_llm_model,
        timeout_seconds=resolved_settings.local_llm_timeout_seconds,
        max_tokens=resolved_settings.local_llm_max_tokens,
    )


def _normalize_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def _coerce_message_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "\n".join(parts)
    raise OpenRouterProviderError("OpenAI-compatible endpoint returned an unsupported message content shape.")


def _should_retry_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504}


def _sleep_backoff(attempt: int, *, retry_after: str | None = None) -> None:
    if retry_after is not None:
        try:
            delay = float(retry_after)
        except ValueError:
            delay = None
        else:
            time.sleep(max(0.0, min(delay, 10.0)))
            return
    time.sleep(min(0.5 * (2**attempt), 4.0))


def _format_error_response(response: httpx.Response) -> str:
    detail = response.text
    try:
        payload = response.json()
    except ValueError:
        pass
    else:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str):
                    detail = message
            elif isinstance(error, str):
                detail = error
    return f"OpenAI-compatible request failed ({response.status_code}): {detail}"
