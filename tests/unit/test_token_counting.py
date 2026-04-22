from __future__ import annotations

from dory_core import token_counting
from dory_core.token_counting import TiktokenCounter


class _OfflineTiktoken:
    def __init__(self) -> None:
        self.calls = 0

    def get_encoding(self, name: str) -> object:
        del name
        self.calls += 1
        raise OSError("offline")


def test_tiktoken_counter_falls_back_when_encoding_cache_needs_network(monkeypatch) -> None:
    offline = _OfflineTiktoken()
    monkeypatch.setattr(token_counting, "tiktoken", offline)
    counter = TiktokenCounter()

    assert counter.count("alpha beta gamma") == 3
    assert counter.count("alpha beta gamma") == 3
    assert offline.calls == 1


def test_tiktoken_counter_falls_back_when_default_encoding_is_unavailable(monkeypatch) -> None:
    offline = _OfflineTiktoken()
    monkeypatch.setattr(token_counting, "tiktoken", offline)
    counter = TiktokenCounter(default_encoding="missing")

    assert counter.count("alpha beta") == 2
