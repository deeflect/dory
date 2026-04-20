from __future__ import annotations

from pathlib import Path

from dory_http import app as http_app


def test_parse_serve_args_uses_expected_defaults() -> None:
    config = http_app.parse_serve_args([])

    assert config.corpus_root == Path(".")
    assert config.index_root == Path(".index")
    assert config.auth_tokens_path == Path(".dory/auth-tokens.json")
    assert config.host == "127.0.0.1"
    assert config.port == 8000


def test_parse_serve_args_reads_environment_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DORY_CORPUS_ROOT", "/tmp/dory-corpus")
    monkeypatch.setenv("DORY_INDEX_ROOT", "/tmp/dory-index")
    monkeypatch.setenv("DORY_AUTH_TOKENS_PATH", "/tmp/dory-auth.json")
    monkeypatch.setenv("DORY_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("DORY_HTTP_PORT", "8766")

    config = http_app.parse_serve_args([])

    assert config.corpus_root == Path("/tmp/dory-corpus")
    assert config.index_root == Path("/tmp/dory-index")
    assert config.auth_tokens_path == Path("/tmp/dory-auth.json")
    assert config.host == "0.0.0.0"
    assert config.port == 8766


def test_main_runs_uvicorn_with_built_app(monkeypatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    def fake_build_app(corpus_root: Path, index_root: Path, auth_tokens_path: Path | None = None) -> object:
        calls["build"] = {
            "corpus_root": corpus_root,
            "index_root": index_root,
            "auth_tokens_path": auth_tokens_path,
        }
        return object()

    def fake_run(app: object, host: str, port: int) -> None:
        calls["run"] = {"app": app, "host": host, "port": port}

    monkeypatch.setattr(http_app, "build_app", fake_build_app)
    monkeypatch.setattr(http_app.uvicorn, "run", fake_run)

    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    auth_tokens_path = tmp_path / "tokens.json"
    http_app.main(
        [
            "--corpus-root",
            str(corpus_root),
            "--index-root",
            str(index_root),
            "--auth-tokens-path",
            str(auth_tokens_path),
            "--host",
            "0.0.0.0",
            "--port",
            "8123",
        ]
    )

    assert calls["build"] == {
        "corpus_root": corpus_root,
        "index_root": index_root,
        "auth_tokens_path": auth_tokens_path,
    }
    assert calls["run"]["host"] == "0.0.0.0"
    assert calls["run"]["port"] == 8123
