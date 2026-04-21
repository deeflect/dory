from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import httpx

from dory_core.config import DorySettings

OpenRouterPurpose = Literal["default", "query", "judge", "dream", "maintenance"]


class OpenRouterConfigurationError(RuntimeError):
    pass


class OpenRouterProviderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OpenRouterModelPricing:
    input_usd_per_million: float
    output_usd_per_million: float


@dataclass(frozen=True, slots=True)
class OpenRouterModelMetadata:
    model: str
    pricing: OpenRouterModelPricing | None
    name: str | None = None
    pricing_source: Literal["env", "live", "none"] = "none"


@dataclass(frozen=True, slots=True)
class OpenRouterClient:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    reasoning_effort: str = "low"
    app_name: str = "dory"
    retries: int = 2

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
            "plugins": [{"id": "response-healing"}],
            "provider": {"require_parameters": True},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": self.app_name,
        }

        response = self._post_chat_completion(headers=headers, payload=payload)

        try:
            payload = response.json()
        except ValueError as err:
            raise OpenRouterProviderError("OpenRouter returned invalid JSON.") from err

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterProviderError("OpenRouter returned no choices.")

        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise OpenRouterProviderError("OpenRouter returned a malformed message payload.")

        content = message.get("content")
        content_text = _coerce_message_content(content)
        try:
            parsed = json.loads(content_text)
        except json.JSONDecodeError as err:
            raise OpenRouterProviderError("OpenRouter returned non-JSON content.") from err
        return parsed

    def _post_chat_completion(self, *, headers: dict[str, str], payload: dict[str, Any]) -> httpx.Response:
        last_error: Exception | None = None
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds) as client:
            for attempt in range(self.retries + 1):
                try:
                    response = client.post("/chat/completions", headers=headers, json=payload)
                except httpx.HTTPError as err:
                    last_error = err
                    if attempt >= self.retries:
                        raise OpenRouterProviderError(f"OpenRouter request failed: {err}") from err
                    self._sleep_backoff(attempt)
                    continue

                if response.status_code < 400:
                    return response
                if not _should_retry_status(response.status_code) or attempt >= self.retries:
                    raise OpenRouterProviderError(_format_error_response(response))
                self._sleep_backoff(attempt, retry_after=response.headers.get("Retry-After"))

        if last_error is not None:
            raise OpenRouterProviderError(f"OpenRouter request failed: {last_error}") from last_error
        raise OpenRouterProviderError("OpenRouter request failed: unknown provider error")

    def _sleep_backoff(self, attempt: int, *, retry_after: str | None = None) -> None:
        if retry_after is not None:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = None
            else:
                time.sleep(max(0.0, min(delay, 10.0)))
                return
        time.sleep(min(0.5 * (2**attempt), 4.0))


def build_openrouter_client(
    settings: DorySettings | None = None,
    *,
    purpose: OpenRouterPurpose = "default",
) -> OpenRouterClient | None:
    resolved_settings = settings or DorySettings()
    if not resolved_settings.openrouter_api_key:
        return None
    return OpenRouterClient(
        api_key=resolved_settings.openrouter_api_key,
        base_url=resolved_settings.openrouter_base_url,
        model=_resolve_model(resolved_settings, purpose),
        timeout_seconds=resolved_settings.openrouter_timeout_seconds,
        reasoning_effort=resolved_settings.openrouter_reasoning_effort,
    )


def require_openrouter_client(
    settings: DorySettings | None = None,
    *,
    purpose: OpenRouterPurpose = "default",
) -> OpenRouterClient:
    client = build_openrouter_client(settings, purpose=purpose)
    if client is None:
        raise OpenRouterConfigurationError(
            "OpenRouter API key is missing. Set DORY_OPENROUTER_API_KEY or OPENROUTER_API_KEY."
        )
    return client


def resolve_openrouter_model_name(
    settings: DorySettings | None = None,
    *,
    purpose: OpenRouterPurpose = "default",
) -> str:
    resolved_settings = settings or DorySettings()
    return _resolve_model(resolved_settings, purpose)


def resolve_openrouter_model_pricing(
    settings: DorySettings | None = None,
    *,
    purpose: OpenRouterPurpose = "default",
) -> OpenRouterModelPricing | None:
    del purpose
    resolved = settings or DorySettings()
    input_rate = resolved.openrouter_input_price_per_million
    output_rate = resolved.openrouter_output_price_per_million
    if input_rate is None or output_rate is None:
        return None
    return OpenRouterModelPricing(
        input_usd_per_million=input_rate,
        output_usd_per_million=output_rate,
    )


def resolve_openrouter_model_metadata(
    settings: DorySettings | None = None,
    *,
    purpose: OpenRouterPurpose = "default",
    use_live_pricing: bool = False,
) -> OpenRouterModelMetadata:
    resolved_settings = settings or DorySettings()
    model = _resolve_model(resolved_settings, purpose)
    env_pricing = resolve_openrouter_model_pricing(resolved_settings, purpose=purpose)
    if env_pricing is not None:
        return OpenRouterModelMetadata(
            model=model,
            pricing=env_pricing,
            pricing_source="env",
        )
    if use_live_pricing:
        live_metadata = _resolve_live_model_metadata(
            base_url=resolved_settings.openrouter_base_url,
            model=model,
            timeout_seconds=resolved_settings.openrouter_timeout_seconds,
        )
        if live_metadata is not None:
            return live_metadata
    return OpenRouterModelMetadata(
        model=model,
        pricing=None,
        pricing_source="none",
    )


@lru_cache(maxsize=4)
def _fetch_openrouter_models_catalog(
    base_url: str,
    timeout_seconds: float,
) -> tuple[dict[str, Any], ...]:
    try:
        with httpx.Client(base_url=base_url, timeout=timeout_seconds) as client:
            response = client.get("/models")
            response.raise_for_status()
    except httpx.HTTPError:
        return ()
    try:
        payload = response.json()
    except ValueError:
        return ()
    data = payload.get("data")
    if not isinstance(data, list):
        return ()
    return tuple(item for item in data if isinstance(item, dict))


def _resolve_live_model_metadata(
    *,
    base_url: str,
    model: str,
    timeout_seconds: float,
) -> OpenRouterModelMetadata | None:
    catalog = _fetch_openrouter_models_catalog(base_url, timeout_seconds)
    match = None
    for item in catalog:
        if item.get("id") == model or item.get("canonical_slug") == model:
            match = item
            break
    if match is None:
        return None
    pricing_payload = match.get("pricing")
    pricing = _pricing_from_live_payload(pricing_payload)
    return OpenRouterModelMetadata(
        model=model,
        name=_coerce_str(match.get("name")),
        pricing=pricing,
        pricing_source="live" if pricing is not None else "none",
    )


def _pricing_from_live_payload(payload: object) -> OpenRouterModelPricing | None:
    if not isinstance(payload, dict):
        return None
    prompt = _per_token_price(payload.get("prompt"))
    completion = _per_token_price(payload.get("completion"))
    if prompt is None or completion is None:
        return None
    return OpenRouterModelPricing(
        input_usd_per_million=prompt * 1_000_000,
        output_usd_per_million=completion * 1_000_000,
    )


def _resolve_model(settings: DorySettings, purpose: OpenRouterPurpose) -> str:
    if purpose == "query":
        return settings.openrouter_query_model or settings.openrouter_model
    if purpose == "judge":
        return settings.openrouter_judge_model or settings.openrouter_model
    if purpose == "dream":
        return settings.openrouter_dream_model or settings.openrouter_model
    if purpose == "maintenance":
        return settings.openrouter_maintenance_model or settings.openrouter_model
    return settings.openrouter_model


def _coerce_message_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        if parts:
            return "\n".join(parts)
    raise OpenRouterProviderError("OpenRouter returned an unsupported message content shape.")


def _coerce_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _format_error_response(response: httpx.Response) -> str:
    detail = response.text.strip()
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                detail = message.strip()
        elif isinstance(error, str) and error.strip():
            detail = error.strip()

    detail = detail or "unknown provider error"
    return f"OpenRouter request failed ({response.status_code}): {detail}"


def _per_token_price(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _should_retry_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600
