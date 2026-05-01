from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from dory_core.frontmatter import load_markdown_document
from dory_http.app_ui import render_app_shell


_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class WikiDocument:
    relative_path: str
    title: str
    frontmatter: dict[str, object]
    body: str


@dataclass(frozen=True, slots=True)
class WikiSearchHit:
    title: str
    relative_path: str
    href: str
    snippet: str


def render_wiki_page(corpus_root: Path, page: str) -> HTMLResponse:
    document = _load_wiki_document(corpus_root, page)
    body_html = _render_markdown_body(document.body)
    meta_html = _render_metadata(document.frontmatter, document.relative_path)
    html = _layout(
        title=document.title,
        content=f'{meta_html}<article class="wiki-doc">{body_html}</article>',
    )
    return HTMLResponse(html)


def render_wiki_search(corpus_root: Path, query: str) -> HTMLResponse:
    normalized_query = query.strip()
    hits = _search_wiki(corpus_root, normalized_query)
    if normalized_query:
        heading = f"Search: {escape(normalized_query)}"
    else:
        heading = "Search the Wiki"
    content = [
        f"<h1>{heading}</h1>",
        _search_form(normalized_query),
    ]
    if normalized_query:
        content.append(f'<p class="muted">{len(hits)} result(s)</p>')
    if hits:
        content.append('<ol class="search-results">')
        for hit in hits:
            content.append(
                "<li>"
                f'<a href="{escape(hit.href, quote=True)}">{escape(hit.title)}</a>'
                f"<code>{escape(hit.relative_path)}</code>"
                f"<p>{escape(hit.snippet)}</p>"
                "</li>"
            )
        content.append("</ol>")
    elif normalized_query:
        content.append("<p>No generated wiki pages matched that query.</p>")
    html = _layout(title="Wiki Search", content="\n".join(content), include_search=False)
    return HTMLResponse(html)


def render_wiki_login(
    *,
    next_path: str = "/wiki",
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    content = (
        '<section class="login-panel">'
        "<h1>Dory Wiki</h1>"
        '<p class="muted">Enter the wiki password to open this browser session.</p>'
        f"{error_html}"
        '<form class="login" action="/wiki/login" method="post">'
        f'<input type="hidden" name="next" value="{escape(next_path, quote=True)}" />'
        "<label>Password"
        '<input name="password" type="password" autocomplete="current-password" autofocus />'
        "</label>"
        '<button type="submit">Open Wiki</button>'
        "</form>"
        "</section>"
    )
    return HTMLResponse(
        _layout(title="Wiki Login", content=content, include_search=False, show_logout=False),
        status_code=status_code,
    )


def _load_wiki_document(corpus_root: Path, page: str) -> WikiDocument:
    target = _resolve_wiki_path(corpus_root, page)
    text = target.read_text(encoding="utf-8")
    try:
        parsed = load_markdown_document(text)
        frontmatter = parsed.frontmatter
        body = parsed.body
    except ValueError:
        frontmatter = {}
        body = text
    relative_path = target.relative_to(corpus_root.resolve()).as_posix()
    title = _document_title(frontmatter, body, target.stem)
    return WikiDocument(
        relative_path=relative_path,
        title=title,
        frontmatter=frontmatter,
        body=body,
    )


def _resolve_wiki_path(corpus_root: Path, page: str) -> Path:
    wiki_root = (corpus_root / "wiki").resolve()
    cleaned = page.strip().strip("/")
    if cleaned in {"", "."}:
        relative_path = Path("index.md")
    else:
        if cleaned.startswith("wiki/"):
            cleaned = cleaned.removeprefix("wiki/")
        relative_path = Path(cleaned)
        if relative_path.suffix != ".md":
            relative_path = relative_path.with_suffix(".md")

    if relative_path.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.parts):
        raise HTTPException(status_code=400, detail=f"invalid wiki path: {page}")

    target = (wiki_root / relative_path).resolve()
    try:
        target.relative_to(wiki_root)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=f"wiki path escapes root: {page}") from err
    if target.suffix != ".md":
        raise HTTPException(status_code=400, detail=f"wiki path is not markdown: {page}")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"wiki page not found: {page or 'index'}")
    return target


def _search_wiki(corpus_root: Path, query: str) -> list[WikiSearchHit]:
    wiki_root = corpus_root / "wiki"
    if not query or not wiki_root.exists():
        return []
    terms = tuple(term.casefold() for term in query.split() if term.strip())
    if not terms:
        return []
    hits: list[WikiSearchHit] = []
    for path in sorted(wiki_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            parsed = load_markdown_document(text)
            frontmatter = parsed.frontmatter
            body = parsed.body
        except ValueError:
            frontmatter = {}
            body = text
        relative_path = path.relative_to(corpus_root).as_posix()
        title = _document_title(frontmatter, body, path.stem)
        haystack = f"{title}\n{relative_path}\n{body}".casefold()
        if not all(term in haystack for term in terms):
            continue
        hits.append(
            WikiSearchHit(
                title=title,
                relative_path=relative_path,
                href=_href_for_wiki_path(relative_path),
                snippet=_snippet_for_query(body, terms),
            )
        )
        if len(hits) >= 50:
            break
    return hits


def _document_title(frontmatter: dict[str, object], body: str, fallback: str) -> str:
    title = frontmatter.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            return match.group(2).strip()
    return fallback.replace("-", " ").replace("_", " ").title()


def _render_metadata(frontmatter: dict[str, object], relative_path: str) -> str:
    chips = [f"<span>{escape(relative_path)}</span>"]
    for key in ("status", "confidence", "updated", "source_path", "source"):
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            chips.append(f"<span>{escape(key)}: {escape(value)}</span>")
    return f'<div class="meta">{"".join(chips)}</div>'


def _render_markdown_body(markdown: str) -> str:
    html: list[str] = []
    in_list = False
    in_code = False
    code_lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith("```"):
            if in_code:
                html.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                if in_list:
                    html.append("</ul>")
                    in_list = False
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            if in_list:
                html.append("</ul>")
                in_list = False
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            if in_list:
                html.append("</ul>")
                in_list = False
            level = min(len(heading.group(1)), 4)
            html.append(f"<h{level}>{_render_inline(heading.group(2))}</h{level}>")
            continue
        if line.startswith("- "):
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{_render_inline(line[2:].strip())}</li>")
            continue
        if line.startswith("> "):
            if in_list:
                html.append("</ul>")
                in_list = False
            html.append(f"<blockquote>{_render_inline(line[2:].strip())}</blockquote>")
            continue
        if in_list:
            html.append("</ul>")
            in_list = False
        html.append(f"<p>{_render_inline(line)}</p>")
    if in_code:
        html.append(f"<pre><code>{escape(chr(10).join(code_lines))}</code></pre>")
    if in_list:
        html.append("</ul>")
    return "\n".join(html)


def _render_inline(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _WIKILINK_RE.finditer(text):
        parts.append(escape(text[cursor : match.start()]))
        target = match.group(1).strip()
        label = (match.group(2) or target).strip()
        href = _href_for_wikilink(target)
        parts.append(f'<a href="{escape(href, quote=True)}">{escape(label)}</a>')
        cursor = match.end()
    parts.append(escape(text[cursor:]))
    return "".join(parts)


def _href_for_wikilink(target: str) -> str:
    cleaned = target.strip().strip("/")
    if cleaned.startswith("wiki/"):
        cleaned = cleaned.removeprefix("wiki/")
    if cleaned.endswith(".md"):
        cleaned = cleaned[:-3]
    return f"/wiki/{quote(cleaned)}"


def _href_for_wiki_path(relative_path: str) -> str:
    cleaned = relative_path
    if cleaned.startswith("wiki/"):
        cleaned = cleaned.removeprefix("wiki/")
    if cleaned.endswith(".md"):
        cleaned = cleaned[:-3]
    if cleaned == "index":
        return "/wiki"
    return f"/wiki/{quote(cleaned)}"


def _snippet_for_query(body: str, terms: tuple[str, ...]) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        folded = stripped.casefold()
        if any(term in folded for term in terms):
            return _plain_snippet(stripped)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return _plain_snippet(stripped)
    return ""


def _plain_snippet(text: str) -> str:
    return _WIKILINK_RE.sub(lambda match: match.group(2) or match.group(1), text)[:240]


def _search_form(query: str = "") -> str:
    return (
        '<form class="search" action="/wiki/search" method="get">'
        f'<input name="q" value="{escape(query, quote=True)}" placeholder="Search generated wiki pages" />'
        '<button type="submit">Search</button>'
        "</form>"
    )


def _layout(*, title: str, content: str, include_search: bool = True, show_logout: bool = True) -> str:
    if include_search:
        search = _search_form()
    else:
        search = None if show_logout else ""
    return render_app_shell(
        title=title,
        active="wiki",
        content=content,
        search_html=search,
        show_logout=show_logout,
    )
