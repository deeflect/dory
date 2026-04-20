from __future__ import annotations

from pathlib import Path

from dory_mcp import server as mcp_server


def test_parse_serve_args_defaults() -> None:
    config = mcp_server.parse_serve_args([])

    assert config.mode == "stdio"
    assert config.host == "127.0.0.1"
    assert config.port == 8765
    assert config.corpus_root == Path(".")
    assert config.index_root == Path(".index")


def test_main_dispatches_tcp(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_serve_tcp(core, host: str, port: int) -> None:
        calls["tcp"] = {"core": core, "host": host, "port": port}

    monkeypatch.setattr(mcp_server, "serve_tcp", fake_serve_tcp)

    mcp_server.main(["--mode", "tcp", "--host", "0.0.0.0", "--port", "9901"])

    assert calls["tcp"]["host"] == "0.0.0.0"
    assert calls["tcp"]["port"] == 9901
