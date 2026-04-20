from __future__ import annotations

import time
from pathlib import Path

from dory_core.openclaw_parity import OpenClawParityStore
from dory_core.index.reindex import ReindexResult
from dory_core.ops import DreamOnceRunner
from dory_core.ops import OpsWatchRunner
from dory_core.watch import WatchCoalescer
from dory_core.types import RecallEventReq


class _FakeClient:
    def generate_json(self, **kwargs):
        return {}


def test_watch_coalescer_waits_for_debounce() -> None:
    coalescer = WatchCoalescer(debounce_seconds=0.5)

    assert coalescer.record("a.md", now=1.0) is False
    assert coalescer.record("b.md", now=1.2) is False
    assert coalescer.ready(now=1.4) is False
    assert coalescer.ready(now=1.8) is True
    assert coalescer.drain() == ["a.md", "b.md"]


def test_dream_once_collects_only_unprocessed_sessions(tmp_path: Path) -> None:
    session = tmp_path / "logs" / "sessions" / "codex" / "2026-04-11.md"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("session body\n", encoding="utf-8")
    distilled = tmp_path / "inbox" / "distilled" / "codex-2026-04-10.md"
    distilled.parent.mkdir(parents=True, exist_ok=True)
    distilled.write_text("old distilled\n", encoding="utf-8")
    proposal = tmp_path / "inbox" / "proposed" / "codex-2026-04-10.json"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("{}\n", encoding="utf-8")

    scan = DreamOnceRunner(tmp_path, _FakeClient()).collect_candidates()

    assert scan.session_paths == ("logs/sessions/codex/2026-04-11.md",)
    assert scan.distilled_paths == ()


def test_dream_once_collects_nested_device_session_paths(tmp_path: Path) -> None:
    session = tmp_path / "logs" / "sessions" / "claude" / "macbook" / "2026-04-12-s1.md"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("session body\n", encoding="utf-8")

    scan = DreamOnceRunner(tmp_path, _FakeClient()).collect_candidates()

    assert scan.session_paths == ("logs/sessions/claude/macbook/2026-04-12-s1.md",)


def test_dream_once_can_skip_recent_session_paths(tmp_path: Path) -> None:
    session = tmp_path / "logs" / "sessions" / "claude" / "macbook" / "2026-04-12-s1.md"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("session body\n", encoding="utf-8")

    scan = DreamOnceRunner(tmp_path, _FakeClient()).collect_candidates(min_session_age_seconds=1800)

    assert scan.session_paths == ()


def test_dream_once_collects_recall_promotion_candidates(tmp_path: Path) -> None:
    store = OpenClawParityStore(tmp_path / ".index")
    for query in ("who is anna", "anna prefs"):
        store.record_recall_event(
            RecallEventReq(
                agent="openclaw",
                session_key="sess-3",
                query=query,
                result_paths=["people/anna.md"],
                selected_path="people/anna.md",
                corpus="memory",
                source="openclaw-recall",
            )
        )

    scan = DreamOnceRunner(tmp_path, _FakeClient(), index_root=tmp_path / ".index").collect_candidates()

    assert scan.recall_paths == ("inbox/distilled/recall-people-anna.md",)
    assert "inbox/distilled/recall-people-anna.md" in scan.distilled_paths


def test_ops_watch_skips_durable_reindex_for_session_only_changes(tmp_path: Path, monkeypatch) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    session = corpus_root / "logs" / "sessions" / "claude" / "macbook" / "2026-04-12-s1.md"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("session body\n", encoding="utf-8")

    called = False

    def _fake_reindex_paths(*args, **kwargs):
        nonlocal called
        called = True
        return ReindexResult(files_indexed=1, chunks_indexed=1, vectors_indexed=1)

    monkeypatch.setattr("dory_core.ops.reindex_paths", _fake_reindex_paths)

    runner = OpsWatchRunner(corpus_root=corpus_root, index_root=index_root, embedder=object(), debounce_seconds=0.5)
    runner.coalescer.record(str(session), now=time.monotonic() - 1.0)

    payload = runner.process_pending()

    assert payload is not None
    assert payload["reindex"]["files_indexed"] == 0
    assert called is False
