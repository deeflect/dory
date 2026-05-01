from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from dory_core.dreaming.proposals import ProposalAction, ProposalDocument, proposal_to_payload
from dory_http.app import build_app
from dory_http.auth import issue_token


def test_app_shell_links_wiki_proposals_and_settings(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    _write_wiki_fixture(corpus_root)
    _write_active_fixture(corpus_root)
    client = TestClient(build_app(corpus_root, index_root, embedder=fake_embedder))
    created = client.post(
        "/v1/memory-proposals",
        json={
            "proposal_id": "ui-proposal",
            "action": "replace",
            "kind": "state",
            "subject": "active",
            "content": "## Current State\n\nUI proposal state.",
            "scope": "core",
        },
    )

    home = client.get("/app")
    wiki = client.get("/wiki")
    proposals = client.get("/app/proposals")
    settings = client.get("/app/settings")

    assert created.status_code == 200, created.text
    assert home.status_code == 200, home.text
    assert 'href="/wiki"' in home.text
    assert 'href="/app/proposals"' in home.text
    assert 'href="/app/settings"' in home.text
    assert "Memory Command Center" in home.text
    assert wiki.status_code == 200, wiki.text
    assert 'aria-current="page">Wiki' in wiki.text
    assert proposals.status_code == 200, proposals.text
    assert "ui-proposal" in proposals.text
    assert 'action="/app/proposals/ui-proposal/apply"' in proposals.text
    assert settings.status_code == 200, settings.text
    assert "Access And Surfaces" in settings.text


def test_app_proposal_detail_shows_every_action(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    _write_wiki_fixture(corpus_root)
    _write_active_fixture(corpus_root)
    proposal = ProposalDocument(
        proposal_id="multi-action",
        source_distilled_path="",
        backend="test",
        actions=[
            ProposalAction(action="write", kind="fact", subject="active", content="First proposed fact."),
            ProposalAction(action="write", kind="decision", subject="active", content="Second proposed decision."),
        ],
    )
    target = corpus_root / "inbox" / "proposed" / "multi-action.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(proposal_to_payload(proposal), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    client = TestClient(build_app(corpus_root, index_root, embedder=fake_embedder))

    page = client.get("/app/proposals", params={"selected": "multi-action"})

    assert page.status_code == 200, page.text
    assert "Action 1: write fact" in page.text
    assert "First proposed fact." in page.text
    assert "Action 2: write decision" in page.text
    assert "Second proposed decision." in page.text


def test_app_proposal_apply_and_reject_forms(tmp_path: Path, fake_embedder) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    _write_wiki_fixture(corpus_root)
    _write_active_fixture(corpus_root)
    client = TestClient(build_app(corpus_root, index_root, embedder=fake_embedder))
    first = client.post(
        "/v1/memory-proposals",
        json={
            "proposal_id": "apply-from-ui",
            "action": "replace",
            "kind": "state",
            "subject": "active",
            "content": "## Current State\n\nApplied from UI.",
            "scope": "core",
        },
    )
    second = client.post(
        "/v1/memory-proposals",
        json={
            "proposal_id": "reject-from-ui",
            "action": "write",
            "kind": "note",
            "subject": "active",
            "content": "Reject from UI.",
            "scope": "core",
        },
    )

    applied = client.post("/app/proposals/apply-from-ui/apply", follow_redirects=False)
    rejected = client.post("/app/proposals/reject-from-ui/reject", follow_redirects=False)
    applied_page = client.get("/app/proposals", params={"status": "applied", "selected": "apply-from-ui"})
    rejected_page = client.get("/app/proposals", params={"status": "rejected", "selected": "reject-from-ui"})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert applied.status_code == 303
    assert applied.headers["location"].startswith("/app/proposals?status=applied")
    assert rejected.status_code == 303
    assert rejected.headers["location"].startswith("/app/proposals?status=rejected")
    assert "Applied from UI." in (corpus_root / "core" / "active.md").read_text(encoding="utf-8")
    assert applied_page.status_code == 200, applied_page.text
    assert "apply-from-ui" in applied_page.text
    assert rejected_page.status_code == 200, rejected_page.text
    assert "reject-from-ui" in rejected_page.text


def test_app_routes_use_browser_auth_redirect(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DORY_ALLOW_NO_AUTH", raising=False)
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    auth_tokens_path = tmp_path / ".dory" / "auth-tokens.json"
    _write_wiki_fixture(corpus_root)
    token = issue_token("browser", auth_tokens_path)
    client = TestClient(build_app(corpus_root, index_root, auth_tokens_path=auth_tokens_path))

    unauthorized = client.get("/app/proposals", follow_redirects=False)
    authorized = client.get("/app/proposals", params={"token": token})
    followup = client.get("/app/settings")

    assert unauthorized.status_code == 303
    assert unauthorized.headers["location"] == "/wiki/login?next=%2Fapp%2Fproposals"
    assert authorized.status_code == 200
    assert followup.status_code == 200


def _write_wiki_fixture(corpus_root: Path) -> None:
    wiki_root = corpus_root / "wiki"
    projects_root = wiki_root / "projects"
    projects_root.mkdir(parents=True)
    (wiki_root / "index.md").write_text(
        "---\ntitle: Wiki Home\ntype: wiki\n---\n\n# Wiki Home\n\n- [[wiki/projects/dory|Dory Project]]\n",
        encoding="utf-8",
    )
    (projects_root / "dory.md").write_text(
        "---\n"
        "title: Dory Project\n"
        "type: wiki\n"
        "status: active\n"
        "updated: 2026-04-19\n"
        "---\n\n"
        "# Dory Project\n\n"
        "Dory keeps durable project state.\n",
        encoding="utf-8",
    )


def _write_active_fixture(corpus_root: Path) -> None:
    (corpus_root / "core").mkdir(parents=True)
    (corpus_root / "core" / "active.md").write_text(
        "---\ntitle: Active\ntype: core\n---\n\n## Current State\n\nOriginal state.\n",
        encoding="utf-8",
    )
