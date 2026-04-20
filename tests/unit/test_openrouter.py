from __future__ import annotations

from dory_core.config import DorySettings
from dory_core.eval_judge import EvalJudgeRequest, OpenRouterEvalJudge
from dory_core.llm.openrouter import OpenRouterClient, build_openrouter_client, resolve_openrouter_model_metadata
from dory_core.query_expansion import OpenRouterQueryExpander


def test_build_openrouter_client_uses_api_key_alias(monkeypatch) -> None:
    monkeypatch.delenv("DORY_OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    client = build_openrouter_client(DorySettings())

    assert client is not None
    assert client.api_key == "test-openrouter-key"
    assert client.model == "google/gemini-3.1-flash-lite-preview"


def test_build_openrouter_client_uses_purpose_specific_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    client = build_openrouter_client(DorySettings(), purpose="query")

    assert client is not None
    assert client.model == "google/gemini-2.5-flash-lite"


def test_query_expander_filters_duplicates() -> None:
    class FakeClient:
        def generate_json(self, **kwargs):
            return {"expansions": ["Clawsy pricing Hetzner", "clawsy pricing hetzner", ""]}

    expander = OpenRouterQueryExpander(client=FakeClient(), max_expansions=3)  # type: ignore[arg-type]

    expanded = expander.expand("Clawzy pricing")

    assert expanded == ["Clawsy pricing Hetzner"]


def test_query_expander_returns_empty_on_non_schema_payload() -> None:
    class FakeClient:
        def generate_json(self, **kwargs):
            return {"content": "- Clawsy pricing Hetzner\n- Clawsy CX22"}

    expander = OpenRouterQueryExpander(client=FakeClient(), max_expansions=3)  # type: ignore[arg-type]

    expanded = expander.expand("Clawzy pricing")

    assert expanded == []


def test_eval_judge_returns_structured_decision() -> None:
    class FakeClient:
        def generate_json(self, **kwargs):
            return {"outcome": "partial", "rationale": "Evidence is mixed."}

    judge = OpenRouterEvalJudge(client=FakeClient())  # type: ignore[arg-type]

    decision = judge.judge(
        EvalJudgeRequest(
            question="Did we ever commit to Postgres?",
            question_type="negation",
            notes="Judge-only case.",
            retrieved_paths=("knowledge/dev/database.md",),
            retrieved_snippets=("Database notes mention Postgres as an option.",),
        )
    )

    assert decision.outcome == "partial"
    assert decision.rationale == "Evidence is mixed."


def test_resolve_openrouter_model_metadata_can_use_live_catalog(monkeypatch) -> None:
    monkeypatch.setattr(
        "dory_core.llm.openrouter._fetch_openrouter_models_catalog",
        lambda base_url, timeout_seconds: (
            {
                "id": "google/gemini-3.1-flash-lite-preview",
                "name": "Google: Gemini 3.1 Flash Lite Preview",
                "pricing": {
                    "prompt": "0.00000025",
                    "completion": "0.0000015",
                },
            },
        ),
    )

    metadata = resolve_openrouter_model_metadata(DorySettings(), purpose="maintenance", use_live_pricing=True)

    assert metadata.model == "google/gemini-3.1-flash-lite-preview"
    assert metadata.name == "Google: Gemini 3.1 Flash Lite Preview"
    assert metadata.pricing is not None
    assert metadata.pricing.input_usd_per_million == 0.25
    assert metadata.pricing.output_usd_per_million == 1.5
    assert metadata.pricing_source == "live"


def test_openrouter_generate_json_enables_response_healing(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, headers, json):
            captured["payload"] = json
            return _Response()

    monkeypatch.setattr("dory_core.llm.openrouter.httpx.Client", _Client)
    client = OpenRouterClient(api_key="k", base_url="https://openrouter.ai/api/v1", model="m", timeout_seconds=30.0)

    result = client.generate_json(system_prompt="s", user_prompt="u", schema_name="Test", schema={"type": "object"})

    assert result == {"ok": True}
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["plugins"] == [{"id": "response-healing"}]


def test_openrouter_generate_json_retries_on_rate_limit(monkeypatch) -> None:
    calls = {"count": 0}

    class _Response:
        def __init__(self, status_code, body, headers=None):
            self.status_code = status_code
            self._body = body
            self.headers = headers or {}
            self.text = body

        def json(self):
            import json as _json

            return _json.loads(self._body)

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, headers, json):
            calls["count"] += 1
            if calls["count"] == 1:
                return _Response(429, '{"error":{"message":"slow down"}}', {"Retry-After": "0"})
            return _Response(200, '{"choices":[{"message":{"content":"{\\"ok\\": true}"}}]}')

    monkeypatch.setattr("dory_core.llm.openrouter.httpx.Client", _Client)
    monkeypatch.setattr("dory_core.llm.openrouter.time.sleep", lambda delay: None)
    client = OpenRouterClient(api_key="k", base_url="https://openrouter.ai/api/v1", model="m", timeout_seconds=30.0)

    result = client.generate_json(system_prompt="s", user_prompt="u", schema_name="Test", schema={"type": "object"})

    assert result == {"ok": True}
    assert calls["count"] == 2
