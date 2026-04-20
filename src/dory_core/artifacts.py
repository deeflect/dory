from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dory_core.embedding import ContentEmbedder
from dory_core.errors import DoryValidationError
from dory_core.fs import resolve_corpus_target
from dory_core.index.reindex import reindex_paths
from dory_core.slug import slugify_path_segment
from dory_core.types import ArtifactReq, ArtifactResp


def resolve_artifact_target(req: ArtifactReq, *, created: str) -> str:
    if req.target:
        return req.target
    slug = slugify_path_segment(req.title)
    match req.kind:
        case "report":
            return f"references/reports/{created}-{slug}.md"
        case "briefing":
            return f"references/briefings/{created}-{slug}.md"
        case "wiki-note":
            return f"wiki/concepts/{slug}.md"
        case "proposal":
            return f"inbox/proposed/{created}-{slug}.md"
    raise ValueError(f"unsupported artifact kind: {req.kind}")


def render_artifact_markdown(req: ArtifactReq, *, created: str) -> str:
    source_lines = _render_sources(req.sources)
    body = req.body.rstrip()
    lines = [
        "---",
        f"title: {req.title}",
        f"created: {created}",
        f"type: {req.kind}",
        f"status: {req.status}",
        "source_kind: generated",
        "temperature: warm",
        f"question: {req.question}",
        "---",
        "",
        f"# {req.title}",
        "",
    ]
    lines.extend(_render_artifact_sections(req.kind, question=req.question, body=body))
    lines.extend([
        "## Sources",
        source_lines,
    ])
    return "\n".join(lines).strip() + "\n"


def render_report_artifact(req: ArtifactReq, *, created: str) -> str:
    return render_artifact_markdown(req, created=created)


def render_briefing_artifact(req: ArtifactReq, *, created: str) -> str:
    return render_artifact_markdown(req, created=created)


def render_wiki_note_artifact(req: ArtifactReq, *, created: str) -> str:
    return render_artifact_markdown(req, created=created)


def render_artifact(req: ArtifactReq, *, created: str) -> str:
    return render_artifact_markdown(req, created=created)


@dataclass(frozen=True, slots=True)
class ArtifactWriter:
    root: Path
    index_root: Path | None = None
    embedder: ContentEmbedder | None = None

    def write(self, req: ArtifactReq, *, created: str) -> ArtifactResp:
        target_rel = _validate_artifact_target(resolve_artifact_target(req, created=created))
        target = resolve_corpus_target(self.root, target_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = render_artifact(req, created=created)
        target.write_text(rendered, encoding="utf-8")
        if self.index_root is not None and self.embedder is not None:
            reindex_paths(
                root=self.root,
                index_root=self.index_root,
                embedder=self.embedder,
                relative_paths=(target_rel.as_posix(),),
            )
        return ArtifactResp(
            path=target_rel.as_posix(),
            kind=req.kind,
            bytes_written=len(rendered.encode("utf-8")),
        )


def _validate_artifact_target(target: str) -> Path:
    target_path = Path(target)
    if target_path.is_absolute() or ".." in target_path.parts:
        raise DoryValidationError("artifact target must be relative to corpus root")
    if target_path.suffix != ".md":
        raise DoryValidationError("artifact target must be a markdown file")
    return target_path


def _render_sources(sources: Iterable[str]) -> str:
    rendered = [f"- {source}" for source in sources]
    return "\n".join(rendered) if rendered else "- None"


def _render_artifact_sections(kind: str, *, question: str, body: str) -> list[str]:
    if kind == "briefing":
        return [
            "## Briefing",
            body,
            "",
            "## Question",
            question,
            "",
        ]
    if kind == "wiki-note":
        return [
            "## Summary",
            body,
            "",
            "## Notes",
            question,
            "",
        ]
    return [
        "## Question",
        question,
        "",
        "## Findings",
        body,
        "",
    ]
