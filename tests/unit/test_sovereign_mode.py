from __future__ import annotations

from dory_core.config import DorySettings
from dory_core.dreaming.extract import resolve_dream_backend


def test_sovereign_mode_prefers_ollama_endpoint() -> None:
    settings = DorySettings(sovereign_mode=True, ollama_base_url="http://127.0.0.1:11434")

    assert resolve_dream_backend(settings) == "ollama"


def test_default_mode_uses_openrouter() -> None:
    settings = DorySettings()

    assert resolve_dream_backend(settings) == "openrouter"
