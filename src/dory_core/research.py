from __future__ import annotations

from dataclasses import dataclass

from dory_core.types import ArtifactKind, ArtifactReq, ResearchReq, ResearchResp, SearchReq


@dataclass(slots=True)
class ResearchEngine:
    search_engine: object

    def research(
        self,
        question: str,
        *,
        kind: ArtifactKind = "report",
        corpus: str = "all",
        limit: int = 8,
    ) -> ResearchResp:
        response = self.search_engine.search(
            SearchReq(
                query=question,
                k=limit,
                mode="hybrid",
                corpus=corpus,
                include_content=False,
                rerank="true",
            )
        )
        results = list(getattr(response, "results", []))[:limit]
        sources = _dedupe_paths(result.path for result in results)
        findings = _render_grounded_findings(question, results)
        artifact = ArtifactReq(
            kind=kind,
            title=question.rstrip("?"),
            question=question,
            body=findings or "No grounded findings.",
            sources=sources,
        )
        return ResearchResp(artifact=artifact, sources=sources)

    def research_from_req(self, req: ResearchReq) -> ResearchResp:
        return self.research(req.question, kind=req.kind, corpus=req.corpus, limit=req.limit)


def _dedupe_paths(paths: object) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for path in paths:
        value = str(path).strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(value)
    return ordered


def _render_grounded_findings(question: str, results: list[object]) -> str:
    if not results:
        return "## Answer\nNo grounded findings.\n"

    answer_lines = _answer_lines(results)
    durable_lines = _evidence_lines(results, session_only=False)
    session_lines = _evidence_lines(results, session_only=True)
    lines = [
        "## Question",
        question.strip(),
        "",
        "## Answer",
        *answer_lines,
        "",
        "## Evidence",
        *(durable_lines or ["- None"]),
    ]
    if session_lines:
        lines.extend(["", "## Session Evidence", *session_lines])
    return "\n".join(lines).strip() + "\n"


def _answer_lines(results: list[object]) -> list[str]:
    snippets: list[str] = []
    seen: set[str] = set()
    for result in results:
        snippet = str(getattr(result, "snippet", "")).strip()
        if not snippet:
            continue
        normalized = snippet.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        snippets.append(f"- {snippet}")
        if len(snippets) >= 3:
            break
    return snippets or ["- No grounded answer."]


def _evidence_lines(results: list[object], *, session_only: bool) -> list[str]:
    lines: list[str] = []
    for result in results:
        path = str(getattr(result, "path", "")).strip()
        if not path:
            continue
        is_session = path.startswith("logs/sessions/")
        if is_session != session_only:
            continue
        snippet = str(getattr(result, "snippet", "")).strip()
        line = f"- {path}"
        if snippet:
            line += f": {snippet}"
        lines.append(line)
    return lines
