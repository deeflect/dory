from __future__ import annotations

from urllib.parse import parse_qs, quote, urlencode

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from dory_core.config import DorySettings
from dory_core.status import build_status
from dory_core.types import MemoryProposalApplyReq, MemoryProposalRejectReq
from dory_http.api_routes import apply_memory_proposal_req, proposal_status_param, reject_memory_proposal_req
from dory_http.app_ui import (
    proposal_counts,
    proposal_view_for,
    render_app_home,
    render_app_proposals,
    render_app_settings,
    wiki_counts,
)
from dory_http.auth import (
    WEB_AUTH_COOKIE,
    WEB_SESSION_COOKIE,
    authorize_web_request,
    login_web_password,
)
from dory_http.runtime import HttpRuntime
from dory_http.wiki import render_wiki_login, render_wiki_page, render_wiki_search
from dory_mcp.tools import build_tool_schemas


def register_web_routes(app: FastAPI, runtime: HttpRuntime, settings: DorySettings) -> None:
    @app.get("/")
    def app_root() -> RedirectResponse:
        return RedirectResponse("/app", status_code=303)

    @app.get("/app", response_class=HTMLResponse)
    def app_home(request: Request) -> Response:
        cookie_token = _authorize_web_or_redirect(request, runtime)
        if isinstance(cookie_token, RedirectResponse):
            return cookie_token
        response = render_app_home(
            status=build_status(runtime.corpus_root, runtime.index_root, settings),
            proposal_counts=proposal_counts(runtime.corpus_root),
            wiki_counts=wiki_counts(runtime.corpus_root),
        )
        _set_legacy_web_auth_cookie(response, request, cookie_token)
        return response

    @app.get("/app/proposals", response_class=HTMLResponse)
    def app_proposals(
        request: Request,
        status: str = Query("pending"),
        selected: str | None = Query(None),
        notice: str | None = Query(None),
    ) -> Response:
        cookie_token = _authorize_web_or_redirect(request, runtime)
        if isinstance(cookie_token, RedirectResponse):
            return cookie_token
        proposal_status = proposal_status_param(status)
        response = render_app_proposals(
            view=proposal_view_for(runtime.corpus_root, status=proposal_status, selected_id=selected),
            notice=notice,
        )
        _set_legacy_web_auth_cookie(response, request, cookie_token)
        return response

    @app.post("/app/proposals/{proposal_id}/apply", response_class=HTMLResponse)
    def app_proposal_apply(proposal_id: str, request: Request) -> Response:
        cookie_token = _authorize_web_or_redirect(request, runtime)
        if isinstance(cookie_token, RedirectResponse):
            return cookie_token
        try:
            apply_memory_proposal_req(
                MemoryProposalApplyReq(
                    proposal_id=proposal_id,
                    agent="dory-web",
                    origin_surface="dory-web",
                ),
                runtime,
            )
        except HTTPException as err:
            response = _proposal_action_error_response(request, runtime, proposal_id, err, cookie_token)
            return response
        return RedirectResponse(
            f"/app/proposals?status=applied&selected={quote(proposal_id, safe='')}&notice=applied",
            status_code=303,
        )

    @app.post("/app/proposals/{proposal_id}/reject", response_class=HTMLResponse)
    def app_proposal_reject(proposal_id: str, request: Request) -> Response:
        cookie_token = _authorize_web_or_redirect(request, runtime)
        if isinstance(cookie_token, RedirectResponse):
            return cookie_token
        try:
            reject_memory_proposal_req(
                MemoryProposalRejectReq(
                    proposal_id=proposal_id,
                    reason="Rejected from Dory web interface.",
                    agent="dory-web",
                    origin_surface="dory-web",
                ),
                runtime,
            )
        except HTTPException as err:
            response = _proposal_action_error_response(request, runtime, proposal_id, err, cookie_token)
            return response
        return RedirectResponse(
            f"/app/proposals?status=rejected&selected={quote(proposal_id, safe='')}&notice=rejected",
            status_code=303,
        )

    @app.get("/app/settings", response_class=HTMLResponse)
    def app_settings(request: Request) -> Response:
        cookie_token = _authorize_web_or_redirect(request, runtime)
        if isinstance(cookie_token, RedirectResponse):
            return cookie_token
        response = render_app_settings(
            status=build_status(runtime.corpus_root, runtime.index_root, settings),
            settings=settings,
            tool_count=len(build_tool_schemas()),
        )
        _set_legacy_web_auth_cookie(response, request, cookie_token)
        return response

    @app.get("/wiki", response_class=HTMLResponse)
    def wiki_index(request: Request) -> Response:
        cookie_token = _authorize_web_or_redirect(request, runtime)
        if isinstance(cookie_token, RedirectResponse):
            return cookie_token
        response = render_wiki_page(runtime.corpus_root, "")
        _set_legacy_web_auth_cookie(response, request, cookie_token)
        return response

    @app.get("/wiki/login", response_class=HTMLResponse)
    def wiki_login(next: str = Query("/wiki")) -> HTMLResponse:
        return render_wiki_login(next_path=_safe_web_next(next))

    @app.post("/wiki/login")
    async def wiki_login_submit(request: Request) -> Response:
        form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
        password = form.get("password", [""])[0]
        next_path = _safe_web_next(form.get("next", ["/wiki"])[0])
        try:
            login = login_web_password(password)
        except HTTPException as err:
            if err.status_code == 401:
                return render_wiki_login(
                    next_path=next_path,
                    error="Invalid password.",
                    status_code=401,
                )
            raise
        response = RedirectResponse(next_path, status_code=303)
        _set_web_session_cookie(response, request, login.session_cookie)
        return response

    @app.get("/wiki/logout")
    def wiki_logout() -> Response:
        response = RedirectResponse("/wiki/login", status_code=303)
        response.delete_cookie(WEB_AUTH_COOKIE)
        response.delete_cookie(WEB_SESSION_COOKIE)
        return response

    @app.get("/wiki/search", response_class=HTMLResponse)
    def wiki_search(
        request: Request,
        q: str = Query(""),
    ) -> Response:
        cookie_token = _authorize_web_or_redirect(request, runtime)
        if isinstance(cookie_token, RedirectResponse):
            return cookie_token
        response = render_wiki_search(runtime.corpus_root, q)
        _set_legacy_web_auth_cookie(response, request, cookie_token)
        return response

    @app.get("/wiki/{page:path}", response_class=HTMLResponse)
    def wiki_page(page: str, request: Request) -> Response:
        cookie_token = _authorize_web_or_redirect(request, runtime)
        if isinstance(cookie_token, RedirectResponse):
            return cookie_token
        response = render_wiki_page(runtime.corpus_root, page)
        _set_legacy_web_auth_cookie(response, request, cookie_token)
        return response


def _authorize_web_or_redirect(
    request: Request,
    runtime: HttpRuntime,
) -> str | RedirectResponse | None:
    try:
        return authorize_web_request(
            request,
            runtime.auth_tokens_path,
            allow_no_auth=runtime.allow_no_auth,
        )
    except HTTPException as err:
        if err.status_code != 401:
            raise
        next_path = _safe_web_next(_request_web_next(request))
        return RedirectResponse(
            f"/wiki/login?{urlencode({'next': next_path})}",
            status_code=303,
        )


def _request_web_next(request: Request) -> str:
    query = [(key, value) for key, value in request.query_params.multi_items() if key != "token"]
    if not query:
        return request.url.path
    return f"{request.url.path}?{urlencode(query)}"


def _safe_web_next(next_path: str) -> str:
    if next_path in {"/wiki", "/app"}:
        return next_path
    if next_path.startswith(("/wiki/", "/wiki?", "/app/", "/app?")) and not next_path.startswith("//"):
        return next_path
    return "/wiki"


def _proposal_action_error_response(
    request: Request,
    runtime: HttpRuntime,
    proposal_id: str,
    err: HTTPException,
    cookie_token: str | None,
) -> HTMLResponse:
    message = _http_error_message(err)
    response = render_app_proposals(
        view=proposal_view_for(runtime.corpus_root, status="pending", selected_id=proposal_id),
        error=message,
    )
    response.status_code = err.status_code
    _set_legacy_web_auth_cookie(response, request, cookie_token)
    return response


def _http_error_message(err: HTTPException) -> str:
    detail = err.detail
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str):
            return message
    return str(detail)


def _set_legacy_web_auth_cookie(response: Response, request: Request, token: str | None) -> None:
    if token is None:
        return
    response.set_cookie(
        WEB_AUTH_COOKIE,
        token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )


def _set_web_session_cookie(response: Response, request: Request, session_cookie: str) -> None:
    response.set_cookie(
        WEB_SESSION_COOKIE,
        session_cookie,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
