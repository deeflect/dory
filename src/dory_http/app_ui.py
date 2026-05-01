from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi.responses import HTMLResponse

from dory_core.config import DorySettings
from dory_core.dreaming.proposals import ProposalDocument, ProposalStore, proposal_to_payload
from dory_core.status import DoryStatus, serialize_status


AppSection = Literal["home", "wiki", "proposals", "settings"]


@dataclass(frozen=True, slots=True)
class ProposalView:
    status: Literal["pending", "applied", "rejected"]
    proposal_ids: tuple[str, ...]
    selected: ProposalDocument | None


def render_app_home(
    *,
    status: DoryStatus,
    proposal_counts: dict[str, int],
    wiki_counts: dict[str, int],
) -> HTMLResponse:
    status_payload = serialize_status(status, debug=True)
    health = "Healthy" if status.index_healthy else "Needs attention"
    content = f"""
      <header class="page-head">
        <div>
          <p class="eyebrow">Dory</p>
          <h1>Memory Command Center</h1>
        </div>
        <span class="status-pill">{escape(health)}</span>
      </header>
      <section class="metric-grid" aria-label="Runtime summary">
        {_metric("Corpus files", status.corpus_files)}
        {_metric("Indexed files", status.files_indexed)}
        {_metric("Chunks", status.chunks_indexed)}
        {_metric("Vectors", status.vectors_indexed)}
      </section>
      <section class="split">
        <div class="panel">
          <div class="panel-head">
            <h2>Proposal Queue</h2>
            <a class="text-link" href="/app/proposals">Open</a>
          </div>
          <dl class="compact-list">
            {_definition("Pending", proposal_counts.get("pending", 0))}
            {_definition("Applied", proposal_counts.get("applied", 0))}
            {_definition("Rejected", proposal_counts.get("rejected", 0))}
          </dl>
        </div>
        <div class="panel">
          <div class="panel-head">
            <h2>Wiki</h2>
            <a class="text-link" href="/wiki">Open</a>
          </div>
          <dl class="compact-list">
            {_definition("Projects", wiki_counts.get("projects", 0))}
            {_definition("People", wiki_counts.get("people", 0))}
            {_definition("Concepts", wiki_counts.get("concepts", 0))}
            {_definition("Decisions", wiki_counts.get("decisions", 0))}
          </dl>
        </div>
      </section>
      <section class="panel">
        <div class="panel-head">
          <h2>Runtime</h2>
          <a class="text-link" href="/app/settings">Settings</a>
        </div>
        <dl class="settings-grid">
          {_definition("Embedding", status_payload.get("embedding_provider", ""))}
          {_definition("Embedding model", status_payload.get("embedding_model", ""))}
          {_definition("Active memory", status_payload.get("active_memory_llm_provider", ""))}
          {_definition("Last reindex", status_payload.get("last_reindex_at") or "none")}
        </dl>
      </section>
    """
    return HTMLResponse(render_app_shell(title="Dory", active="home", content=content))


def render_app_proposals(
    *,
    view: ProposalView,
    notice: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    selected = view.selected
    rows = "\n".join(_proposal_row(proposal_id, view.status, selected) for proposal_id in view.proposal_ids)
    if not rows:
        rows = '<li class="empty-row">No proposals in this state.</li>'
    detail = _proposal_detail(selected, view.status)
    alert = _alert("notice", notice) if notice else _alert("error", error) if error else ""
    content = f"""
      <header class="page-head">
        <div>
          <p class="eyebrow">Review Queue</p>
          <h1>Memory Proposals</h1>
        </div>
      </header>
      {alert}
      <nav class="tabs" aria-label="Proposal status">
        {_tab("Pending", "pending", view.status)}
        {_tab("Applied", "applied", view.status)}
        {_tab("Rejected", "rejected", view.status)}
      </nav>
      <section class="two-pane">
        <aside class="proposal-list" aria-label="Proposals">
          <ol>{rows}</ol>
        </aside>
        <div class="proposal-detail">
          {detail}
        </div>
      </section>
    """
    return HTMLResponse(render_app_shell(title="Memory Proposals", active="proposals", content=content))


def render_app_settings(
    *,
    status: DoryStatus,
    settings: DorySettings,
    tool_count: int,
) -> HTMLResponse:
    status_payload = serialize_status(status, debug=True)
    content = f"""
      <header class="page-head">
        <div>
          <p class="eyebrow">Runtime</p>
          <h1>Settings</h1>
        </div>
        <a class="button secondary" href="/v1/status?debug=true">JSON</a>
      </header>
      <section class="split">
        <div class="panel">
          <div class="panel-head"><h2>Storage</h2></div>
          <dl class="settings-grid">
            {_definition("Corpus root", status_payload.get("corpus_root", ""))}
            {_definition("Index root", status_payload.get("index_root", ""))}
            {_definition("Index present", _yes_no(status.index_present))}
            {_definition("Index stale", _yes_no(status.index_stale))}
            {_definition("Missing files", status.index_missing_files)}
            {_definition("Vector drift", status.vector_drift)}
          </dl>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Models</h2></div>
          <dl class="settings-grid">
            {_definition("Embedding provider", status.embedding_provider)}
            {_definition("Embedding dimensions", status.embedding_dimensions)}
            {_definition("Reranker", _yes_no(status.query_reranker_enabled))}
            {_definition("Reranker provider", status.query_reranker_provider or "off")}
            {_definition("Active memory", status.active_memory_llm_provider)}
            {_definition("Active stages", status.active_memory_llm_stages)}
          </dl>
        </div>
      </section>
      <section class="panel">
        <div class="panel-head"><h2>Access And Surfaces</h2></div>
        <dl class="settings-grid">
          {_definition("Bearer auth", "off" if settings.allow_no_auth else "required")}
          {_definition("Web password", "set" if settings.web_password else "not set")}
          {_definition("HTTP bind", f"{settings.http_host}:{settings.http_port}")}
          {_definition("MCP tools", tool_count)}
          {_definition("OpenClaw parity", "enabled" if status.openclaw.recall_tracking_enabled else "off")}
          {_definition("API version", status.api_version)}
        </dl>
      </section>
    """
    return HTMLResponse(render_app_shell(title="Settings", active="settings", content=content))


def render_app_shell(
    *,
    title: str,
    active: AppSection,
    content: str,
    search_html: str | None = None,
    show_logout: bool = True,
) -> str:
    nav = "".join(
        _nav_link(label, href, key, active)
        for label, href, key in (
            ("Home", "/app", "home"),
            ("Wiki", "/wiki", "wiki"),
            ("Proposals", "/app/proposals", "proposals"),
            ("Settings", "/app/settings", "settings"),
        )
    )
    utility = search_html if search_html is not None else '<a class="button secondary" href="/wiki/search">Search Wiki</a>'
    logout = '<a class="button secondary" href="/wiki/logout">Logout</a>' if show_logout else ""
    title_suffix = "Dory Wiki" if active == "wiki" else "Dory"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)} - {title_suffix}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: oklch(0.965 0.008 95);
      --surface: oklch(0.988 0.006 95);
      --surface-2: oklch(0.948 0.009 95);
      --ink: oklch(0.205 0.016 75);
      --muted: oklch(0.475 0.018 75);
      --line: oklch(0.865 0.012 88);
      --accent: oklch(0.48 0.13 35);
      --accent-soft: oklch(0.93 0.035 45);
      --danger: oklch(0.47 0.15 25);
      --success: oklch(0.46 0.105 150);
      --code-bg: oklch(0.925 0.012 84);
      --focus: oklch(0.58 0.16 35);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      line-height: 1.48;
    }}
    a {{ color: var(--accent); text-underline-offset: .16em; }}
    button, input, textarea {{ font: inherit; }}
    .app-shell {{
      display: grid;
      grid-template-columns: 15.5rem minmax(0, 1fr);
      min-height: 100vh;
    }}
    .side-nav {{
      background: var(--surface-2);
      border-right: 1px solid var(--line);
      padding: 1rem;
      position: sticky;
      top: 0;
      height: 100vh;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: .65rem;
      min-height: 2.5rem;
      margin-bottom: 1rem;
      font-weight: 750;
    }}
    .mark {{
      display: inline-grid;
      place-items: center;
      width: 1.8rem;
      height: 1.8rem;
      border: 1px solid var(--line);
      border-radius: .45rem;
      background: var(--surface);
      color: var(--accent);
      font-weight: 850;
    }}
    .side-nav nav a {{
      display: flex;
      align-items: center;
      min-height: 2.2rem;
      color: var(--ink);
      text-decoration: none;
      padding: .42rem .58rem;
      border-radius: .45rem;
      margin-bottom: .12rem;
      font-size: .94rem;
    }}
    .side-nav nav a:hover, .side-nav nav a[aria-current="page"] {{
      background: var(--accent-soft);
      color: var(--ink);
    }}
    .content-shell {{
      min-width: 0;
    }}
    .top-bar {{
      min-height: 3.75rem;
      border-bottom: 1px solid var(--line);
      background: color-mix(in oklch, var(--surface) 86%, transparent);
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: .75rem;
      padding: .7rem clamp(1rem, 3vw, 2rem);
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    main {{
      max-width: 1180px;
      padding: 1.65rem clamp(1rem, 3vw, 2rem) 3rem;
    }}
    .page-head {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1.2rem;
    }}
    .eyebrow {{
      margin: 0 0 .28rem;
      color: var(--muted);
      font-size: .78rem;
      font-weight: 750;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    h1, h2, h3 {{
      margin: 0;
      line-height: 1.15;
      letter-spacing: 0;
    }}
    h1 {{ font-size: 2rem; font-weight: 760; }}
    h2 {{ font-size: 1.02rem; font-weight: 720; }}
    h3 {{ font-size: .94rem; font-weight: 720; }}
    p, li, dd, dt {{ font-size: .95rem; }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: .5rem;
      padding: 1rem;
    }}
    .panel-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: .85rem;
    }}
    .split, .metric-grid {{
      display: grid;
      gap: .85rem;
      margin-bottom: .85rem;
    }}
    .split {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .metric-grid {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .metric {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: .5rem;
      padding: .85rem;
    }}
    .metric span, .compact-list dt, .settings-grid dt {{
      color: var(--muted);
      font-size: .78rem;
      font-weight: 680;
    }}
    .metric strong {{
      display: block;
      margin-top: .25rem;
      font-size: 1.35rem;
      line-height: 1.1;
    }}
    .compact-list, .settings-grid {{
      display: grid;
      gap: .55rem .85rem;
      margin: 0;
    }}
    .compact-list div, .settings-grid div {{
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      border-top: 1px solid var(--line);
      padding-top: .55rem;
    }}
    .settings-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .settings-grid div {{
      display: grid;
      justify-content: stretch;
      gap: .18rem;
      min-width: 0;
    }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    .button, button {{
      border: 1px solid var(--accent);
      border-radius: .45rem;
      background: var(--accent);
      color: var(--surface);
      min-height: 2.15rem;
      padding: .42rem .72rem;
      text-decoration: none;
      font-weight: 720;
      cursor: pointer;
    }}
    .button.secondary, button.secondary {{
      background: var(--surface);
      border-color: var(--line);
      color: var(--ink);
    }}
    .button.danger, button.danger {{
      background: var(--surface);
      border-color: color-mix(in oklch, var(--danger) 45%, var(--line));
      color: var(--danger);
    }}
    .text-link {{
      font-size: .88rem;
      font-weight: 720;
    }}
    .status-pill, .chip {{
      display: inline-flex;
      align-items: center;
      min-height: 1.75rem;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: .22rem .62rem;
      background: var(--surface);
      color: var(--muted);
      font-size: .82rem;
      font-weight: 700;
    }}
    .tabs {{
      display: flex;
      gap: .35rem;
      border-bottom: 1px solid var(--line);
      margin-bottom: .85rem;
    }}
    .tabs a {{
      color: var(--muted);
      text-decoration: none;
      padding: .6rem .75rem;
      border-radius: .45rem .45rem 0 0;
      font-weight: 720;
    }}
    .tabs a[aria-current="page"] {{
      color: var(--ink);
      background: var(--surface);
      border: 1px solid var(--line);
      border-bottom-color: var(--surface);
      margin-bottom: -1px;
    }}
    .two-pane {{
      display: grid;
      grid-template-columns: minmax(14rem, 22rem) minmax(0, 1fr);
      gap: .85rem;
      align-items: start;
    }}
    .proposal-list {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: .5rem;
      overflow: hidden;
    }}
    .proposal-list ol {{
      list-style: none;
      margin: 0;
      padding: 0;
    }}
    .proposal-list a, .empty-row {{
      display: block;
      padding: .72rem .8rem;
      color: var(--ink);
      text-decoration: none;
      border-bottom: 1px solid var(--line);
    }}
    .proposal-list a[aria-current="page"], .proposal-list a:hover {{
      background: var(--accent-soft);
    }}
    .proposal-detail {{
      min-width: 0;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: .5rem;
      padding: 1rem;
    }}
    .action-row {{
      display: flex;
      gap: .5rem;
      flex-wrap: wrap;
      margin: .9rem 0;
    }}
    .proposal-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: .35rem;
      margin: .7rem 0;
    }}
    .proposal-action {{
      border-top: 1px solid var(--line);
      margin-top: .9rem;
      padding-top: .9rem;
    }}
    .proposal-action h3 {{
      margin-bottom: .55rem;
    }}
    .search {{
      display: flex;
      gap: .5rem;
      margin: 0;
    }}
    .search input {{
      min-width: min(34vw, 24rem);
      border: 1px solid var(--line);
      border-radius: .45rem;
      background: var(--surface);
      color: var(--ink);
      min-height: 2.15rem;
      padding: .42rem .62rem;
    }}
    article.wiki-doc {{
      max-width: 74ch;
    }}
    article.wiki-doc h1 {{ margin-bottom: .8rem; }}
    article.wiki-doc h2 {{
      margin-top: 1.5rem;
      padding-top: .9rem;
      border-top: 1px solid var(--line);
    }}
    article.wiki-doc h3 {{ margin-top: 1.2rem; }}
    article.wiki-doc p, article.wiki-doc li {{ font-size: .98rem; }}
    code {{
      font-family: "SFMono-Regular", "Cascadia Mono", monospace;
      font-size: .84em;
      background: var(--code-bg);
      border-radius: .3rem;
      padding: .08rem .3rem;
    }}
    pre {{
      overflow: auto;
      background: oklch(0.24 0.018 75);
      color: oklch(0.94 0.008 95);
      border-radius: .45rem;
      padding: .85rem;
    }}
    pre code {{ background: transparent; color: inherit; padding: 0; }}
    blockquote {{
      margin: .9rem 0;
      padding: .75rem .85rem;
      color: var(--muted);
      background: var(--surface-2);
      border: 1px solid var(--line);
      border-radius: .45rem;
    }}
    .meta, .alert {{
      display: flex;
      flex-wrap: wrap;
      gap: .35rem;
      margin-bottom: .9rem;
    }}
    .meta span {{
      border: 1px solid var(--line);
      background: var(--surface-2);
      color: var(--muted);
      border-radius: 999px;
      padding: .16rem .5rem;
      font-size: .78rem;
    }}
    .alert {{
      display: block;
      border: 1px solid var(--line);
      border-radius: .5rem;
      padding: .7rem .8rem;
      background: var(--surface);
    }}
    .alert.notice {{ border-color: color-mix(in oklch, var(--success) 42%, var(--line)); color: var(--success); }}
    .alert.error {{ border-color: color-mix(in oklch, var(--danger) 42%, var(--line)); color: var(--danger); }}
    .muted, .search-results p {{ color: var(--muted); }}
    .search-results code {{ margin-left: .45rem; }}
    .login-panel {{ max-width: 28rem; }}
    .login label {{
      display: grid;
      gap: .35rem;
      color: var(--muted);
      font-weight: 700;
    }}
    .login input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: .45rem;
      padding: .68rem .72rem;
      background: var(--surface);
      color: var(--ink);
      margin-bottom: .75rem;
    }}
    .login button {{ width: 100%; }}
    :focus-visible {{
      outline: 2px solid var(--focus);
      outline-offset: 2px;
    }}
    @media (max-width: 900px) {{
      .app-shell {{ grid-template-columns: 1fr; }}
      .side-nav {{
        position: static;
        height: auto;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}
      .side-nav nav {{
        display: flex;
        gap: .25rem;
        overflow-x: auto;
        padding-bottom: .2rem;
      }}
      .side-nav nav a {{ white-space: nowrap; }}
      .top-bar {{ position: static; justify-content: flex-start; }}
      .split, .metric-grid, .two-pane, .settings-grid {{ grid-template-columns: 1fr; }}
      .search {{ width: 100%; }}
      .search input {{ min-width: 0; width: 100%; }}
    }}
  </style>
</head>
<body>
  <div class="app-shell">
    <aside class="side-nav">
      <div class="brand"><span class="mark">D</span><span>Dory</span></div>
      <nav>{nav}</nav>
    </aside>
    <div class="content-shell">
      <header class="top-bar">{utility}{logout}</header>
      <main>{content}</main>
    </div>
  </div>
</body>
</html>
"""


def proposal_view_for(
    corpus_root: Path,
    *,
    status: Literal["pending", "applied", "rejected"],
    selected_id: str | None = None,
) -> ProposalView:
    store = ProposalStore(corpus_root)
    proposal_ids = tuple(store.list(status=status))
    selected: ProposalDocument | None = None
    target_id = selected_id if selected_id in proposal_ids else proposal_ids[0] if proposal_ids else None
    if target_id is not None:
        selected = store.load(target_id, status=status)
    return ProposalView(status=status, proposal_ids=proposal_ids, selected=selected)


def proposal_counts(corpus_root: Path) -> dict[str, int]:
    store = ProposalStore(corpus_root)
    return {status: len(store.list(status=status)) for status in ("pending", "applied", "rejected")}


def wiki_counts(corpus_root: Path) -> dict[str, int]:
    wiki_root = corpus_root / "wiki"
    return {
        family: len(list((wiki_root / family).glob("*.md"))) if (wiki_root / family).exists() else 0
        for family in ("projects", "people", "concepts", "decisions")
    }


def _proposal_detail(
    proposal: ProposalDocument | None,
    status: Literal["pending", "applied", "rejected"],
) -> str:
    if proposal is None:
        return '<p class="muted">No proposal selected.</p>'
    payload = proposal_to_payload(proposal)
    action_blocks = _proposal_action_blocks(proposal)
    actions = ""
    if status == "pending":
        actions = f"""
          <div class="action-row">
            <form method="post" action="/app/proposals/{quote(proposal.proposal_id, safe='')}/apply">
              <button type="submit">Apply</button>
            </form>
            <form method="post" action="/app/proposals/{quote(proposal.proposal_id, safe='')}/reject">
              <button class="danger" type="submit">Reject</button>
            </form>
          </div>
        """
    return f"""
      <div class="panel-head">
        <h2>{escape(proposal.proposal_id)}</h2>
        <span class="status-pill">{escape(proposal.status)}</span>
      </div>
      <div class="proposal-meta">
        {_chip(f"kind: {proposal.proposal_kind}")}
        {_chip(f"backend: {proposal.backend}")}
        {_chip(f"agent: {proposal.agent or 'unknown'}")}
      </div>
      {actions}
      <dl class="settings-grid">
        {_definition("Created", payload.get("created_at") or "unknown")}
        {_definition("Origin", proposal.origin_surface or "unknown")}
        {_definition("Reason", proposal.reason or "none")}
        {_definition("Sources", ", ".join(proposal.source_paths or []) or "none")}
      </dl>
      {action_blocks}
    """


def _proposal_action_blocks(proposal: ProposalDocument) -> str:
    if not proposal.actions:
        return '<p class="muted">This proposal has no actions.</p>'
    blocks: list[str] = []
    for index, action in enumerate(proposal.actions, start=1):
        risk = json.dumps(action.risk or {}, indent=2, sort_keys=True)
        dry_run = json.dumps(action.dry_run or {}, indent=2, sort_keys=True)
        blocks.append(
            f"""
            <section class="proposal-action">
              <h3>Action {index}: {escape(action.action)} {escape(action.kind)}</h3>
              <dl class="settings-grid">
                {_definition("Subject", action.subject)}
                {_definition("Scope", action.scope or "auto")}
                {_definition("Confidence", action.confidence or "auto")}
                {_definition("Source", action.source or "none")}
              </dl>
              <h3>Content</h3>
              <pre><code>{escape(action.content)}</code></pre>
              <h3>Risk</h3>
              <pre><code>{escape(risk)}</code></pre>
              <h3>Dry Run</h3>
              <pre><code>{escape(dry_run)}</code></pre>
            </section>
            """
        )
    return "\n".join(blocks)


def _proposal_row(
    proposal_id: str,
    status: Literal["pending", "applied", "rejected"],
    selected: ProposalDocument | None,
) -> str:
    current = selected is not None and selected.proposal_id == proposal_id
    aria = ' aria-current="page"' if current else ""
    href = f"/app/proposals?status={quote(status, safe='')}&selected={quote(proposal_id, safe='')}"
    return f'<li><a href="{href}"{aria}>{escape(proposal_id)}</a></li>'


def _metric(label: str, value: object) -> str:
    return f'<div class="metric"><span>{escape(label)}</span><strong>{escape(str(value))}</strong></div>'


def _definition(label: str, value: object) -> str:
    return f"<div><dt>{escape(label)}</dt><dd>{escape(str(value))}</dd></div>"


def _tab(label: str, value: str, active: str) -> str:
    aria = ' aria-current="page"' if value == active else ""
    return f'<a href="/app/proposals?status={quote(value, safe="")}"{aria}>{escape(label)}</a>'


def _nav_link(label: str, href: str, key: str, active: AppSection) -> str:
    aria = ' aria-current="page"' if key == active else ""
    return f'<a href="{href}"{aria}>{escape(label)}</a>'


def _chip(text: str) -> str:
    return f'<span class="chip">{escape(text)}</span>'


def _alert(kind: str, message: str | None) -> str:
    if not message:
        return ""
    return f'<div class="alert {escape(kind)}">{escape(message)}</div>'


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
