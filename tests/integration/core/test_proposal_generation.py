from __future__ import annotations

import json
from pathlib import Path

from dory_core.config import DorySettings
from dory_core.dreaming.extract import resolve_dream_backend
from dory_core.dreaming.proposals import (
    ProposalAction,
    ProposalDocument,
    ProposalGenerator,
    ProposalStore,
    apply_proposal,
    create_semantic_write_proposal,
    proposal_to_payload,
    reject_proposal,
)
from dory_core.errors import DoryValidationError
from dory_core.semantic_write import SemanticWriteEngine
from dory_core.types import MemoryProposalCreateReq


def test_proposal_generator_writes_reviewable_json(tmp_path: Path) -> None:
    distilled_path = tmp_path / "inbox" / "distilled" / "codex-2026-04-07.md"
    distilled_path.parent.mkdir(parents=True, exist_ok=True)
    distilled_path.write_text("A distilled summary for review.\n", encoding="utf-8")

    generator = ProposalGenerator(
        root=tmp_path,
        backend=resolve_dream_backend(DorySettings()),
    )

    target = generator.generate(distilled_path)

    assert target == tmp_path / "inbox" / "proposed" / "codex-2026-04-07.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["backend"] == "openrouter"
    assert payload["source_distilled_path"] == str(distilled_path)
    assert payload["actions"] == []


def test_proposal_generator_uses_openrouter_actions_when_available(tmp_path: Path) -> None:
    class FakeClient:
        def generate_json(self, **kwargs):
            return {
                "actions": [
                    {
                        "action": "write",
                        "kind": "decision",
                        "subject": "atlas",
                        "content": "Pricing moved to BYOK tiers.",
                        "scope": "project",
                        "confidence": "high",
                        "reason": "Grounded in distilled session facts.",
                        "source": "dream-proposal",
                        "soft": False,
                    }
                ]
            }

    distilled_path = tmp_path / "inbox" / "distilled" / "codex-2026-04-10.md"
    distilled_path.parent.mkdir(parents=True, exist_ok=True)
    distilled_path.write_text("Distilled summary.\n", encoding="utf-8")

    generator = ProposalGenerator(
        root=tmp_path,
        backend=resolve_dream_backend(DorySettings()),
        client=FakeClient(),  # type: ignore[arg-type]
    )

    target = generator.generate(distilled_path)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["actions"][0]["action"] == "write"
    assert payload["actions"][0]["kind"] == "decision"
    assert payload["actions"][0]["subject"] == "atlas"


def test_semantic_write_proposal_create_apply_and_reject(tmp_path: Path, fake_embedder) -> None:
    root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (root / "core").mkdir(parents=True)
    (root / "core" / "active.md").write_text(
        "---\ntitle: Active\ntype: core\n---\n\n## Current State\n\nOriginal active state.\n",
        encoding="utf-8",
    )
    engine = SemanticWriteEngine(root, index_root=index_root, embedder=fake_embedder)

    proposal, path = create_semantic_write_proposal(
        root=root,
        engine=engine,
        req=MemoryProposalCreateReq(
            proposal_id="active-update",
            action="replace",
            kind="state",
            subject="active",
            content="## Current State\n\nReviewable proposal state.",
            scope="core",
            confidence="high",
            agent="codex",
            origin_surface="test",
        ),
    )

    assert path == root / "inbox" / "proposed" / "active-update.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["proposal_kind"] == "semantic-write"
    assert payload["agent"] == "codex"
    assert payload["actions"][0]["dry_run"]["target_path"] == "core/active.md"
    assert "Reviewable proposal state." not in (root / "core" / "active.md").read_text(encoding="utf-8")

    result = apply_proposal(root=root, engine=engine, proposal_id=proposal.proposal_id)

    assert result.applied == ("core/active.md",)
    assert not path.exists()
    assert (root / "inbox" / "applied" / "active-update.json").exists()
    assert "Reviewable proposal state." in (root / "core" / "active.md").read_text(encoding="utf-8")

    rejected, rejected_path = create_semantic_write_proposal(
        root=root,
        engine=engine,
        req=MemoryProposalCreateReq(
            proposal_id="active-reject",
            action="write",
            kind="note",
            subject="active",
            content="Reject this proposal.",
            scope="core",
        ),
    )
    archived = reject_proposal(root=root, proposal_id=rejected.proposal_id, reason="not needed")
    archived_payload = json.loads((root / archived).read_text(encoding="utf-8"))
    assert not rejected_path.exists()
    assert archived == "inbox/rejected/active-reject.json"
    assert archived_payload["status"] == "rejected"
    assert archived_payload["rejected_reason"] == "not needed"


def test_semantic_write_proposal_apply_rejects_stale_dry_run(tmp_path: Path, fake_embedder) -> None:
    root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    (root / "core").mkdir(parents=True)
    (root / "core" / "active.md").write_text(
        "---\ntitle: Active\ntype: core\n---\n\n## Current State\n\nOriginal active state.\n",
        encoding="utf-8",
    )
    engine = SemanticWriteEngine(root, index_root=index_root, embedder=fake_embedder)
    _proposal, path = create_semantic_write_proposal(
        root=root,
        engine=engine,
        req=MemoryProposalCreateReq(
            proposal_id="stale-active-update",
            action="replace",
            kind="state",
            subject="active",
            content="## Current State\n\nStale candidate.",
            scope="core",
        ),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["actions"][0]["dry_run"]["target_path"] = "core/wrong.md"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    try:
        apply_proposal(root=root, engine=engine, proposal_id="stale-active-update")
    except DoryValidationError as err:
        assert "stale proposal action" in str(err)
    else:
        raise AssertionError("expected stale proposal validation failure")

    assert path.exists()
    assert "Stale candidate." not in (root / "core" / "active.md").read_text(encoding="utf-8")


def test_semantic_write_proposal_can_apply_forced_inbox_capture(tmp_path: Path, fake_embedder) -> None:
    root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    engine = SemanticWriteEngine(root, index_root=index_root, embedder=fake_embedder)

    proposal, path = create_semantic_write_proposal(
        root=root,
        engine=engine,
        req=MemoryProposalCreateReq(
            proposal_id="forced-inbox-capture",
            action="write",
            kind="note",
            subject="unresolved capture",
            content="Reviewable inbox capture.",
            force_inbox=True,
        ),
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["actions"][0]["force_inbox"] is True
    assert payload["actions"][0]["dry_run"]["target_path"].startswith("inbox/semantic/")

    result = apply_proposal(root=root, engine=engine, proposal_id=proposal.proposal_id)

    assert result.applied[0].startswith("inbox/semantic/")
    assert "Reviewable inbox capture." in (root / result.applied[0]).read_text(encoding="utf-8")


def test_semantic_write_proposal_ids_are_single_file_queue_entries(tmp_path: Path, fake_embedder) -> None:
    root = tmp_path / "corpus"
    index_root = tmp_path / "index"
    engine = SemanticWriteEngine(root, index_root=index_root, embedder=fake_embedder)

    proposal, path = create_semantic_write_proposal(
        root=root,
        engine=engine,
        req=MemoryProposalCreateReq(
            proposal_id="../Nested Proposal",
            action="write",
            kind="note",
            subject="unresolved capture",
            content="Reviewable inbox capture.",
            force_inbox=True,
        ),
    )

    assert proposal.proposal_id == "nested-proposal"
    assert path == root / "inbox" / "proposed" / "nested-proposal.json"

    try:
        create_semantic_write_proposal(
            root=root,
            engine=engine,
            req=MemoryProposalCreateReq(
                proposal_id="nested-proposal",
                action="write",
                kind="note",
                subject="unresolved capture",
                content="Duplicate.",
                force_inbox=True,
            ),
        )
    except DoryValidationError as err:
        assert "proposal already exists" in str(err)
    else:
        raise AssertionError("expected duplicate proposal validation failure")


def test_proposal_archive_refuses_to_overwrite_existing_archive(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    pending = ProposalDocument(
        proposal_id="duplicate-archive",
        source_distilled_path="",
        backend="test",
        actions=[
            ProposalAction(
                action="write",
                kind="note",
                subject="active",
                content="Pending content.",
            )
        ],
    )
    existing = ProposalDocument(
        proposal_id="duplicate-archive",
        source_distilled_path="",
        backend="test",
        actions=[
            ProposalAction(
                action="write",
                kind="note",
                subject="active",
                content="Existing archive content.",
            )
        ],
        status="applied",
    )
    store = ProposalStore(root)
    pending_path = store.write_pending(pending)
    archive_path = root / "inbox" / "applied" / "duplicate-archive.json"
    archive_path.parent.mkdir(parents=True)
    archive_path.write_text(
        json.dumps(proposal_to_payload(existing), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    try:
        store.archive(pending_path, pending, status="applied")
    except DoryValidationError as err:
        assert "archived proposal already exists" in str(err)
    else:
        raise AssertionError("expected archive overwrite validation failure")

    assert pending_path.exists()
    assert "Existing archive content." in archive_path.read_text(encoding="utf-8")
