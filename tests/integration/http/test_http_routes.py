from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient

from dory_core.index.reindex import reindex_corpus
from dory_core.types import WriteReq
from dory_core.write import WriteEngine
from dory_http.app import build_app


def test_http_routes_cover_core_verbs(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    reindex_corpus(corpus_root, index_root, fake_embedder)
    WriteEngine(root=corpus_root, index_root=index_root, embedder=fake_embedder).write(
        WriteReq(
            kind="append",
            target="knowledge/links.md",
            content="See [[people/alex|Alex]].",
            frontmatter={"title": "Links", "type": "knowledge"},
        )
    )

    client = TestClient(build_app(corpus_root, index_root))

    wake = client.post("/v1/wake", json={"agent": "codex", "budget_tokens": 200})
    search = client.post("/v1/search", json={"query": "HomeServer"})
    text_search = client.post("/v1/search", json={"query": "HomeServer", "mode": "text"})
    get = client.get("/v1/get", params={"path": "core/user.md", "from": 1, "lines": 8})
    tools = client.get("/v1/tools")
    write = client.post(
        "/v1/write",
        json={
            "kind": "append",
            "target": "inbox/http.md",
            "content": "HTTP write path works.",
            "frontmatter": {"title": "HTTP note", "type": "capture"},
        },
    )
    purge_dry_run = client.post("/v1/purge", json={"target": "inbox/http.md"})
    link = client.post("/v1/link", json={"op": "neighbors", "path": "knowledge/links.md"})

    assert wake.status_code == 200
    assert "block" in wake.json()
    assert wake.json()["profile"] == "default"
    assert search.status_code == 200
    assert search.json()["count"] >= 1
    assert text_search.status_code == 200
    assert text_search.json()["count"] >= 1
    assert get.status_code == 200
    assert "Casey builds agent infrastructure" in get.json()["content"]
    assert tools.status_code == 200
    assert any(tool["name"] == "dory_purge" for tool in tools.json()["tools"])
    assert write.status_code == 200
    assert write.json()["path"] == "inbox/http.md"
    assert purge_dry_run.status_code == 200
    assert purge_dry_run.json()["action"] == "would_purge"
    assert link.status_code == 200
    assert link.json()["count"] >= 1


def test_http_purge_requires_hash_for_live_delete(
    tmp_path: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    target = corpus_root / "inbox" / "http-purge.md"
    target.parent.mkdir(parents=True)
    text = "---\ntitle: HTTP purge\ntype: capture\n---\n\nTemporary HTTP artifact.\n"
    target.write_text(text, encoding="utf-8")
    reindex_corpus(corpus_root, index_root, fake_embedder)
    client = TestClient(build_app(corpus_root, index_root))

    missing_hash = client.post(
        "/v1/purge",
        json={"target": "inbox/http-purge.md", "dry_run": False, "reason": "cleanup"},
    )
    deleted = client.post(
        "/v1/purge",
        json={
            "target": "inbox/http-purge.md",
            "dry_run": False,
            "reason": "cleanup",
            "expected_hash": f"sha256:{sha256(text.encode('utf-8')).hexdigest()}",
        },
    )

    assert missing_hash.status_code == 400
    assert "expected_hash" in missing_hash.json()["detail"]["message"]
    assert deleted.status_code == 200
    assert deleted.json()["action"] == "purged"
    assert not target.exists()


def test_http_write_returns_400_for_business_validation_errors(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    reindex_corpus(corpus_root, index_root, fake_embedder)
    client = TestClient(build_app(corpus_root, index_root))

    response = client.post(
        "/v1/write",
        json={
            "kind": "append",
            "target": "inbox/http-invalid.md",
            "content": "Missing frontmatter should be a request error, not a server crash.",
        },
    )

    assert response.status_code == 400
    assert "frontmatter is required" in response.json()["detail"]["message"]


def test_http_link_rejects_path_escape(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    reindex_corpus(corpus_root, index_root, fake_embedder)
    client = TestClient(build_app(corpus_root, index_root))

    response = client.post("/v1/link", json={"op": "neighbors", "path": "../outside.md"})

    assert response.status_code == 400
    assert "escapes corpus root" in response.json()["detail"]
