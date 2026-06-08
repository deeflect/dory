from __future__ import annotations

import time
from pathlib import Path

from dory_core.claim_store import ClaimStore
from dory_core.openclaw_parity import OpenClawParityStore
from dory_core.index.reindex import ReindexResult
from dory_core.ops import DreamOnceRunner
from dory_core.ops import OpsWatchRunner
from dory_core.ops import run_memory_activity
from dory_core.ops import run_observation_refresh
from dory_core.observation_retrieval import ObservationRetrieval
from dory_core.observation_store import ObservationStore
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


def test_dream_once_collects_unprocessed_digests_by_default(tmp_path: Path) -> None:
    session = tmp_path / "logs" / "sessions" / "codex" / "2026-04-11.md"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("session body\n", encoding="utf-8")
    digest = tmp_path / "digests" / "daily" / "2026-04-11.md"
    digest.parent.mkdir(parents=True, exist_ok=True)
    digest.write_text("daily digest\n", encoding="utf-8")
    old_digest = tmp_path / "digests" / "daily" / "2026-04-10.md"
    old_digest.write_text("old digest\n", encoding="utf-8")
    proposal = tmp_path / "inbox" / "proposed" / "2026-04-10.json"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("{}\n", encoding="utf-8")

    scan = DreamOnceRunner(tmp_path, _FakeClient()).collect_candidates()

    assert scan.session_paths == ()
    assert scan.digest_paths == ("digests/daily/2026-04-11.md",)
    assert scan.distilled_paths == ()


def test_dream_once_collects_session_paths_only_when_requested(tmp_path: Path) -> None:
    session = tmp_path / "logs" / "sessions" / "claude" / "macbook" / "2026-04-12-s1.md"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("session body\n", encoding="utf-8")

    default_scan = DreamOnceRunner(tmp_path, _FakeClient()).collect_candidates()
    session_scan = DreamOnceRunner(tmp_path, _FakeClient()).collect_candidates(include_sessions=True)

    assert default_scan.session_paths == ()
    assert session_scan.session_paths == ("logs/sessions/claude/macbook/2026-04-12-s1.md",)


def test_dream_once_can_skip_recent_session_paths(tmp_path: Path) -> None:
    session = tmp_path / "logs" / "sessions" / "claude" / "macbook" / "2026-04-12-s1.md"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("session body\n", encoding="utf-8")

    scan = DreamOnceRunner(tmp_path, _FakeClient()).collect_candidates(
        include_sessions=True,
        min_session_age_seconds=1800,
    )

    assert scan.session_paths == ()


def test_dream_once_collects_recall_promotion_candidates(tmp_path: Path) -> None:
    store = OpenClawParityStore(tmp_path / ".index")
    for query in ("who is avery", "avery prefs"):
        store.record_recall_event(
            RecallEventReq(
                agent="openclaw",
                session_key="sess-3",
                query=query,
                result_paths=["people/avery.md"],
                selected_path="people/avery.md",
                corpus="memory",
                source="openclaw-recall",
            )
        )

    scan = DreamOnceRunner(tmp_path, _FakeClient(), index_root=tmp_path / ".index").collect_candidates()

    assert scan.recall_paths == ("inbox/distilled/recall-people-avery.md",)
    assert "inbox/distilled/recall-people-avery.md" in scan.distilled_paths


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


def test_observation_refresh_noops_without_claim_store(tmp_path: Path) -> None:
    payload = run_observation_refresh(tmp_path)

    assert payload == {
        "claim_store_found": False,
        "observations_created": 0,
        "source_claims": 0,
        "observation_store": ".dory/observation-store.db",
    }
    assert not (tmp_path / ".dory" / "observation-store.db").exists()


def test_observation_refresh_rebuilds_from_claim_store(tmp_path: Path) -> None:
    claim_store = ClaimStore(tmp_path / ".dory" / "claim-store.db")
    claim_store.add_claim(
        entity_id="project:dory",
        kind="state",
        statement="Dory observations are derived from active claims.",
        evidence_path="projects/dory/state.md",
    )

    payload = run_observation_refresh(tmp_path)

    assert payload["claim_store_found"] is True
    assert payload["observations_created"] == 1
    retrieval = ObservationRetrieval(ObservationStore(tmp_path / ".dory" / "observation-store.db"))
    observations = retrieval.find_by_entity("project:dory")
    assert len(observations) == 1
    assert observations[0].content == "Dory observations are derived from active claims."


def test_memory_activity_reports_claims_observations_and_proposals(tmp_path: Path) -> None:
    claim_store = ClaimStore(tmp_path / ".dory" / "claim-store.db")
    claim_store.add_claim(
        entity_id="project:dory",
        kind="state",
        statement="Dory memory activity should be reviewable.",
        evidence_path="projects/dory/state.md",
    )
    run_observation_refresh(tmp_path)
    proposed = tmp_path / "inbox" / "proposed" / "proposal-1.json"
    proposed.parent.mkdir(parents=True)
    proposed.write_text("{}\n", encoding="utf-8")

    payload = run_memory_activity(tmp_path, limit=5)

    assert payload["claim_store_found"] is True
    assert payload["observation_store_found"] is True
    assert payload["observations"] == {"active": 1, "stale": 0, "total": 1}
    assert payload["proposals"] == {"pending": 1, "applied": 0, "rejected": 0}
    events = payload["recent_claim_events"]
    assert isinstance(events, list)
    assert events[0]["entity_id"] == "project:dory"
    assert events[0]["statement"] == "Dory memory activity should be reviewable."
