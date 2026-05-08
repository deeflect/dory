from __future__ import annotations

import json
from pathlib import Path


OPENCLAW_PACKAGE_ROOT = Path("packages/openclaw-dory")
EXPECTED_CONFIG_FIELDS = {"baseUrl", "token", "tokenEnv", "timeoutMs"}


def _read_package_text(relative: str) -> str:
    return (OPENCLAW_PACKAGE_ROOT / relative).read_text(encoding="utf-8")


def test_openclaw_plugin_manifest_declares_memory_slot() -> None:
    manifest_path = Path("packages/openclaw-dory/openclaw.plugin.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["id"] == "dory-memory"
    assert payload["kind"] == "memory"
    assert payload["configSchema"]["required"] == ["baseUrl"]
    assert EXPECTED_CONFIG_FIELDS <= set(payload["configSchema"]["properties"])
    assert "entry" not in payload
    assert (manifest_path.parent / "dist" / "index.js").exists()


def test_openclaw_plugin_package_declares_sdk_entrypoint() -> None:
    package_path = Path("packages/openclaw-dory/package.json")
    payload = json.loads(package_path.read_text(encoding="utf-8"))

    assert payload["name"] == "dory-memory"
    assert payload["openclaw"]["extensions"] == ["./dist/index.js"]


def test_openclaw_plugin_source_exports_sdk_registration_contract() -> None:
    source = _read_package_text("src/index.ts")

    assert 'from "openclaw/plugin-sdk/plugin-entry"' in source
    assert "definePluginEntry({" in source
    assert "registerMemoryCapability" in source
    assert "class DoryMemorySearchManager" in source
    assert "promptBuilder:" in source
    assert 'kind: "memory"' in source
    assert 'name: "memory_write"' in source
    assert "/v1/memory-write" in source
    assert "/v1/recall-event" in source
    assert "/v1/active-memory" in source
    assert "/v1/public-artifacts" in source
    assert "mapDoryCorpus" in source
    assert 'corpus: mapDoryCorpus(opts?.corpus)' in source
    assert "session_key: opts.sessionKey" in source
    assert "scope: opts?.sessionKey ? { session_key: opts.sessionKey } : undefined" in source
    assert "tokenEnv" in source
    assert "subject" in source
    assert "confidence" in source
    assert "source" in source
    assert "dry_run" in source
    assert "force_inbox" in source
    assert "allow_canonical" in source
    assert "flushPlanResolver:" in source
    assert 'relativePath: "openclaw/compaction-flush.md"' in source


def test_openclaw_plugin_does_not_request_search_debug_by_default() -> None:
    source = _read_package_text("src/index.ts")

    assert "debug: opts?.debug ?? false" in source
    assert "debug: opts?.debug ?? true" not in source
    assert "delete cleaned._doryOrder" in source


def test_openclaw_source_and_dist_preserve_session_search_contract() -> None:
    surfaces = {
        "source": _read_package_text("src/index.ts"),
        "dist": _read_package_text("dist/index.js"),
    }

    for surface, text in surfaces.items():
        assert 'corpus: { enum: ["memory", "wiki", "all", "sessions"] }' in text, surface
        assert "corpus: requestedCorpus" in text, surface
        assert "function mapDoryCorpus" in text, surface
        assert 'if (corpus === "sessions")' in text, surface
        assert "sessionKeyApplied: Boolean(opts?.sessionKey)" in text, surface
        assert "sessionKeySupported: true" in text, surface
        assert "sessionKeySupported: false" not in text, surface
        assert "sessionKey is not yet supported" not in text, surface
        assert text.count("scope: opts?.sessionKey ? { session_key: opts.sessionKey } : undefined") >= 2, surface
        assert "project: opts?.project" in text, surface


def test_openclaw_manifest_readme_source_and_dist_config_contract_match() -> None:
    manifest = json.loads((OPENCLAW_PACKAGE_ROOT / "openclaw.plugin.json").read_text(encoding="utf-8"))
    manifest_fields = set(manifest["configSchema"]["properties"])
    readme = _read_package_text("README.md")
    source = _read_package_text("src/index.ts")
    dist = _read_package_text("dist/index.js")

    assert manifest_fields == EXPECTED_CONFIG_FIELDS
    for field in EXPECTED_CONFIG_FIELDS:
        assert field in readme, field
        assert field in source, field
        assert field in dist, field

    assert "timeout_ms" in source
    assert "timeout_ms" in dist
