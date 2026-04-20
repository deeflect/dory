from __future__ import annotations

from dory_core.config import DorySettings
from dory_core.llm.openai_compatible import OpenAICompatibleJSONClient, build_local_llm_client


def test_build_local_llm_client_requires_key() -> None:
    client = build_local_llm_client(DorySettings(local_llm_api_key=None))

    assert client is None


def test_build_local_llm_client_uses_settings() -> None:
    client = build_local_llm_client(
        DorySettings(
            local_llm_api_key="test",
            local_llm_base_url="https://llm.example.test",
            local_llm_model="Qwen3.5-4B-4bit",
            local_llm_timeout_seconds=4.0,
            local_llm_max_tokens=256,
        )
    )

    assert client is not None
    assert client.api_key == "test"
    assert client.base_url == "https://llm.example.test"
    assert client.model == "Qwen3.5-4B-4bit"
    assert client.timeout_seconds == 4.0
    assert client.max_tokens == 256


def test_openai_compatible_generate_json_uses_strict_schema(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    class _Client:
        def __init__(self, *args, **kwargs):
            captured["base_url"] = kwargs.get("base_url") if "base_url" in kwargs else args[0]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json
            return _Response()

    monkeypatch.setattr("dory_core.llm.openai_compatible.httpx.Client", _Client)
    client = OpenAICompatibleJSONClient(
        api_key="test",
        base_url="https://llm.example.test",
        model="local-model",
        timeout_seconds=3.0,
    )

    result = client.generate_json(
        system_prompt="system",
        user_prompt="user",
        schema_name="test_schema",
        schema={"type": "object"},
    )

    assert result == {"ok": True}
    assert captured["base_url"] == "https://llm.example.test/v1"
    assert captured["url"] == "/chat/completions"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "local-model"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 512
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "test_schema",
            "strict": True,
            "schema": {"type": "object"},
        },
    }
