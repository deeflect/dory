from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

from dory_core.frontmatter import load_markdown_document
from dory_core.index.reindex import reindex_corpus
from dory_http.app import build_app
from dory_http.auth import issue_token


def _load_provider_class() -> type:
    provider_path = Path("plugins/hermes-dory/provider.py")
    spec = importlib.util.spec_from_file_location("hermes_dory_provider", provider_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load provider module from {provider_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.DoryMemoryProvider


def _disable_query_llm(monkeypatch) -> None:
    monkeypatch.setenv("DORY_QUERY_EXPANSION_ENABLED", "false")
    monkeypatch.setenv("DORY_QUERY_PLANNER_ENABLED", "false")
    monkeypatch.setenv("DORY_QUERY_RERANKER_ENABLED", "false")


def test_hermes_provider_covers_http_verbs(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DORY_ALLOW_NO_AUTH", raising=False)
    _disable_query_llm(monkeypatch)
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    auth_tokens_path = tmp_path / ".dory" / "auth-tokens.json"

    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    reindex_corpus(corpus_root, index_root, fake_embedder)
    token = issue_token("hermes", auth_tokens_path)
    client = TestClient(
        build_app(corpus_root, index_root, auth_tokens_path=auth_tokens_path, embedder=fake_embedder)
    )
    provider_cls = _load_provider_class()
    provider = provider_cls(
        base_url=str(client.base_url),
        token=token,
        client=client,
    )

    wake = provider.wake(agent="hermes", budget_tokens=200, profile="coding")
    search = provider.search("HomeServer")
    exact_search = provider.search("HomeServer", mode="exact")
    thresholded_search = provider.search("HomeServer", min_score=100.0)
    scoped_search = provider.search(
        "HomeServer",
        corpus="durable",
        scope={"path_glob": "core/*"},
        include_content=False,
    )
    active_memory = provider.active_memory("what are we working on today", agent="hermes", include_wake=False)
    read = provider.get("core/user.md", lines=8)
    semantic_preview = provider.memory_write(
        action="write",
        kind="fact",
        subject="alex",
        content="Alex prefers async work.",
        scope="person",
        dry_run=True,
    )
    semantic_write = provider.memory_write(
        action="write",
        kind="fact",
        subject="alex",
        content="Alex prefers async work.",
        scope="person",
        soft=True,
        allow_canonical=True,
    )
    semantic_forget = provider.memory_write(
        action="forget",
        kind="fact",
        subject="alex",
        content="Alex prefers async work.",
        scope="person",
        reason="superseded",
        allow_canonical=True,
    )
    forced_inbox_preview = provider.memory_write(
        action="write",
        kind="note",
        subject="scratch-hermes-provider",
        content="Hermes scratch note.",
        force_inbox=True,
        dry_run=True,
    )
    write_preview = provider.write(
        kind="create",
        target="inbox/hermes-preview.md",
        content="Hermes provider dry run.",
        dry_run=True,
        frontmatter={"title": "Hermes preview", "type": "capture"},
    )
    write = provider.write(
        kind="append",
        target="inbox/hermes.md",
        content="Hermes provider writes.",
        frontmatter={"title": "Hermes note", "type": "capture"},
    )
    purge_preview = provider.purge(target="inbox/hermes.md")
    research = provider.research("What mentions HomeServer?", save=False)
    link = provider.link({"op": "neighbors", "path": "core/user.md"})
    status = provider.status()

    assert "block" in wake
    assert search["count"] >= 1
    assert exact_search["count"] >= 1
    assert exact_search["results"][0]["score_normalized"] is not None
    assert thresholded_search["count"] == 0
    assert scoped_search["count"] >= 1
    assert "content" not in scoped_search["results"][0]
    assert active_memory["kind"] in {"memory", "none"}
    assert "summary" in active_memory
    assert "Casey builds agent infrastructure" in read["content"]
    assert semantic_preview["result"] == "preview"
    assert semantic_write["resolved"] is True
    assert semantic_write["target_path"] == "people/alex.md"
    assert semantic_forget["resolved"] is True
    assert semantic_forget["result"] == "forgotten"
    assert forced_inbox_preview["result"] == "preview"
    assert write_preview["action"] == "would_create"
    semantic_artifacts = sorted((corpus_root / "sources" / "semantic").rglob("*.md"))
    assert len(semantic_artifacts) == 2
    artifact_frontmatters = [
        load_markdown_document(path.read_text(encoding="utf-8")).frontmatter
        for path in semantic_artifacts
    ]
    assert {frontmatter["action"] for frontmatter in artifact_frontmatters} == {
        "write",
        "forget",
    }
    assert all(frontmatter["source_kind"] == "semantic" for frontmatter in artifact_frontmatters)
    assert write["path"] == "inbox/hermes.md"
    assert purge_preview["action"] == "would_purge"
    assert purge_preview["dry_run"] is True
    assert research["artifact"] is None
    assert research["research"]["sources"]
    assert isinstance(link, dict)
    assert status["files_indexed"] == 9


def test_hermes_provider_supports_hermes_plugin_surface(
    tmp_path: Path,
    sample_corpus_root: Path,
    fake_embedder,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DORY_ALLOW_NO_AUTH", raising=False)
    _disable_query_llm(monkeypatch)
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    auth_tokens_path = tmp_path / ".dory" / "auth-tokens.json"

    for source in sample_corpus_root.rglob("*.md"):
        target = corpus_root / source.relative_to(sample_corpus_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    reindex_corpus(corpus_root, index_root, fake_embedder)
    token = issue_token("hermes", auth_tokens_path)
    client = TestClient(
        build_app(corpus_root, index_root, auth_tokens_path=auth_tokens_path, embedder=fake_embedder)
    )
    provider_cls = _load_provider_class()
    provider = provider_cls(
        base_url=str(client.base_url),
        token=token,
        client=client,
        default_agent="hermes",
        search_k=4,
    )
    provider.initialize(
        "session-123",
        hermes_home=str(tmp_path / ".hermes"),
        platform="cli",
        agent_context="primary",
        agent_identity="coder",
    )

    prefetched = provider.prefetch_bundle("who is Casey")
    memory_section = provider.build_memory_section("who is Casey")
    injected_section = provider.prefetch("who is Casey", session_id="session-123")
    tool_schemas = provider.get_tool_schemas()
    search_tool_result = json.loads(provider.handle_tool_call("dory_search", {"query": "HomeServer"}))
    active_tool_result = json.loads(
        provider.handle_tool_call(
            "dory_active_memory",
            {"prompt": "what are we working on today", "include_wake": False},
        )
    )
    research_tool_result = json.loads(
        provider.handle_tool_call(
            "dory_research",
            {"question": "What mentions HomeServer?", "save": False},
        )
    )
    write_tool_result = json.loads(
        provider.handle_tool_call(
            "dory_write",
            {
                "kind": "create",
                "target": "inbox/hermes-tool-preview.md",
                "content": "preview only",
                "dry_run": True,
                "frontmatter": {"title": "Hermes tool preview", "type": "capture"},
            },
        )
    )
    purge_tool_result = json.loads(
        provider.handle_tool_call("dory_purge", {"target": "people/alex.md", "allow_canonical": True})
    )
    provider.sync_turn(
        "What are we working on today?",
        "We are focused on the HomeServer deployment.",
        session_id="session-123",
    )
    provider.on_session_end(
        [
            {"role": "user", "content": "Remember my async preferences."},
            {"role": "assistant", "content": "I will keep that in mind."},
        ]
    )
    provider.on_memory_write("add", "memory", "Hermes mirrored memory write.")
    writes = provider.sync_memories(
        [
            {
                "subject": "alex",
                "action": "replace",
                "kind": "state",
                "scope": "person",
                "content": "Alex is now focused on semantic memory routing.",
                "allow_canonical": True,
            },
            {
                "target": "inbox/hermes-sync.md",
                "content": "Synced durable memory.",
                "frontmatter": {"title": "Hermes sync", "type": "capture"},
            }
        ]
    )

    assert "wake" in prefetched
    assert "search" in prefetched
    assert "active_memory" in prefetched
    assert "# Dory Memory" in memory_section
    assert "# Dory Memory" in injected_section
    assert "core/user.md" in memory_section
    assert {schema["name"] for schema in tool_schemas} >= {
        "dory_search",
        "dory_get",
        "dory_memory_write",
        "dory_research",
        "dory_purge",
    }
    assert search_tool_result["count"] >= 1
    assert active_tool_result["kind"] in {"memory", "none"}
    assert research_tool_result["artifact"] is None
    assert write_tool_result["action"] == "would_create"
    assert purge_tool_result["action"] == "would_purge"
    assert len(writes) == 2
    assert writes[0]["resolved"] is True
    assert writes[0]["target_path"] == "people/alex.md"
    assert writes[1]["path"] == "inbox/hermes-sync.md"
    session_logs = sorted((corpus_root / "logs" / "sessions" / "hermes").rglob("*.md"))
    assert len(session_logs) == 1
    session_text = session_logs[0].read_text(encoding="utf-8")
    assert "Remember my async preferences." in session_text
    assert "I will keep that in mind." in session_text
    mirrored_memory_paths = sorted(corpus_root.rglob("*.md"))
    assert any(
        "Hermes mirrored memory write." in path.read_text(encoding="utf-8")
        for path in mirrored_memory_paths
    )
