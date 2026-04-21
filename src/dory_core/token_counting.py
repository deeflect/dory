from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

try:
    import tiktoken
except ImportError:  # pragma: no cover - dependency is present in runtime installs
    tiktoken = None

_DEFAULT_ENCODING = "o200k_base"
_AGENT_ENCODINGS = {
    "claude": "cl100k_base",
    "claude-code": "cl100k_base",
    "codex": "o200k_base",
    "openai": "o200k_base",
    "opencode": "o200k_base",
    "openclaw": "cl100k_base",
    "hermes": "cl100k_base",
}


class TokenCounter(Protocol):
    def count(self, text: str, *, agent: str = "default") -> int: ...


@dataclass(slots=True)
class HeuristicTokenCounter:
    def count(self, text: str, *, agent: str = "default") -> int:
        del agent
        stripped = text.strip()
        if not stripped:
            return 0
        return len(stripped.split())


@dataclass(slots=True)
class TiktokenCounter:
    default_encoding: str = _DEFAULT_ENCODING
    agent_encodings: dict[str, str] = field(default_factory=dict)
    _cache: dict[str, object] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        # Dynamic DORY_WAKE_TOKEN_ENCODING_<agent> prefix can't be declared in
        # DorySettings without changing the user-facing env shape, so env iteration
        # stays here by design.
        merged = dict(_AGENT_ENCODINGS)
        merged.update(self.agent_encodings)
        for key, value in os.environ.items():
            if key == "DORY_WAKE_TOKEN_ENCODING":
                self.default_encoding = value
                continue
            if key.startswith("DORY_WAKE_TOKEN_ENCODING_"):
                agent = key.removeprefix("DORY_WAKE_TOKEN_ENCODING_").lower().replace("_", "-")
                merged[agent] = value
        self.agent_encodings = merged

    def count(self, text: str, *, agent: str = "default") -> int:
        stripped = text.strip()
        if not stripped:
            return 0
        encoding = self._resolve_encoding(agent)
        return len(encoding.encode(stripped, disallowed_special=()))

    def _resolve_encoding(self, agent: str) -> object:
        encoding_name = self.agent_encodings.get(agent.lower(), self.default_encoding)
        if encoding_name not in self._cache:
            try:
                self._cache[encoding_name] = tiktoken.get_encoding(encoding_name)
            except KeyError:
                self._cache[encoding_name] = tiktoken.get_encoding(self.default_encoding)
        return self._cache[encoding_name]


def build_token_counter() -> TokenCounter:
    if tiktoken is None:
        return HeuristicTokenCounter()
    return TiktokenCounter()
