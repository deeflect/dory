from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dory_http.app import build_app
from dory_http.auth import WEB_AUTH_COOKIE, WEB_SESSION_COOKIE, issue_token


def test_wiki_route_renders_generated_markdown(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    _write_wiki_fixture(corpus_root)
    client = TestClient(build_app(corpus_root, index_root))

    index = client.get("/wiki")
    page = client.get("/wiki/projects/dory")
    search = client.get("/wiki/search", params={"q": "durable"})

    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert "Dory Wiki" in index.text
    assert 'href="/wiki/projects/dory"' in index.text
    assert page.status_code == 200
    assert "Dory Project" in page.text
    assert "durable project state" in page.text
    assert search.status_code == 200
    assert "wiki/projects/dory.md" in search.text


def test_wiki_route_rejects_path_escape(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    _write_wiki_fixture(corpus_root)
    client = TestClient(build_app(corpus_root, index_root))

    response = client.get("/wiki/%2E%2E/core/user")

    assert response.status_code == 400


def test_wiki_route_supports_browser_cookie_auth(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DORY_ALLOW_NO_AUTH", raising=False)
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    auth_tokens_path = tmp_path / ".dory" / "auth-tokens.json"
    _write_wiki_fixture(corpus_root)
    token = issue_token("browser", auth_tokens_path)
    client = TestClient(build_app(corpus_root, index_root, auth_tokens_path=auth_tokens_path))

    unauthorized = client.get("/wiki", follow_redirects=False)
    login = client.get("/wiki", params={"token": token})
    followup = client.get("/wiki/projects/dory")

    assert unauthorized.status_code == 303
    assert unauthorized.headers["location"] == "/wiki/login?next=%2Fwiki"
    assert login.status_code == 200
    assert client.cookies.get(WEB_AUTH_COOKIE) == token
    assert followup.status_code == 200
    assert "Dory Project" in followup.text


def test_wiki_login_sets_signed_session_cookie(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DORY_ALLOW_NO_AUTH", raising=False)
    corpus_root = tmp_path / "corpus"
    index_root = tmp_path / ".index"
    _write_wiki_fixture(corpus_root)
    monkeypatch.setenv("DORY_WEB_PASSWORD", "test-wiki-password")
    client = TestClient(build_app(corpus_root, index_root))

    redirect = client.get("/wiki/projects/dory", follow_redirects=False)
    login_form = client.get("/wiki/login")
    bad_login = client.post(
        "/wiki/login",
        data={"password": "wrong", "next": "/wiki/projects/dory"},
        follow_redirects=False,
    )
    good_login = client.post(
        "/wiki/login",
        data={"password": "test-wiki-password", "next": "/wiki/projects/dory"},
        follow_redirects=False,
    )
    followup = client.get("/wiki/projects/dory")

    assert redirect.status_code == 303
    assert redirect.headers["location"] == "/wiki/login?next=%2Fwiki%2Fprojects%2Fdory"
    assert login_form.status_code == 200
    assert 'type="password"' in login_form.text
    assert bad_login.status_code == 401
    assert "Invalid password" in bad_login.text
    assert good_login.status_code == 303
    assert good_login.headers["location"] == "/wiki/projects/dory"
    assert client.cookies.get(WEB_SESSION_COOKIE)
    assert followup.status_code == 200
    assert "Dory Project" in followup.text


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
