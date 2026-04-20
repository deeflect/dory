"""Mine claims out of daily/weekly digests into the claim store.

The rich signal in a corpus often lives in daily digests: Decisions Made,
Preferences Learned, Patterns Observed, Project state changes, People
facts. This module turns those narrative sections into individual
``ClaimRecord`` entries on canonical entity pages (core:*, person:*,
project:*, decision:*) with provenance back to the digest.

The extractor is LLM-driven. No regex for "Decisions Made: -" bullets,
because the real corpus uses narrative merged digests that don't have
structured bullet sections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, Sequence

from dory_core.claim_store import ClaimStore
from dory_core.frontmatter import load_markdown_document
from dory_core.llm.openrouter import OpenRouterClient, OpenRouterProviderError


ClaimKind = Literal["fact", "preference", "state", "decision", "note"]
_ALLOWED_KINDS: frozenset[str] = frozenset({"fact", "preference", "state", "decision", "note"})
_ALLOWED_CONFIDENCE: frozenset[str] = frozenset({"high", "medium", "low"})


@dataclass(frozen=True, slots=True)
class DigestClaim:
    subject_ref: str
    kind: ClaimKind
    statement: str
    confidence: str
    time_ref: str | None
    evidence_path: str
    reason: str | None = None


class DigestExtractor(Protocol):
    def extract(
        self,
        *,
        digest_text: str,
        digest_date: str | None,
        evidence_path: str,
    ) -> list[DigestClaim]: ...


_SYSTEM_PROMPT = (
    "You extract durable claims from a personal-memory daily or weekly digest.\n"
    "A digest is a narrative record of a day or week of work: projects worked on, "
    "decisions made, preferences learned, patterns observed, people interactions.\n\n"
    "For each durable claim in the digest, emit a structured entry:\n"
    "- subject_ref: where the claim belongs. Use one of:\n"
    "    core:user | core:soul | core:env | core:identity | core:active | core:defaults\n"
    "    person:<slug>   (e.g. person:primary-user, person:collaborator)\n"
    "    project:<slug>  (e.g. project:rooster-spec, project:clawsy)\n"
    "    decision:<slug> (stable descriptive slug)\n"
    "    concept:<slug>  (only if the claim is clearly a concept/pattern)\n"
    "- kind: one of fact | preference | state | decision | note\n"
    "    fact       = durable factual claim about an entity\n"
    "    preference = how the user prefers things (goes on core:user or core:soul)\n"
    "    state      = project state change (active/paused/done)\n"
    "    decision   = a decision made with rationale\n"
    "    note       = observation or pattern worth remembering\n"
    "- statement: the claim text in one sentence, present tense\n"
    "- confidence: high | medium | low (how clearly the digest supports the claim)\n"
    "- time_ref: ISO date (YYYY-MM-DD) or null\n"
    "- reason: brief extraction rationale (1 sentence)\n\n"
    "Rules:\n"
    "1. Extract ONLY claims the digest actually supports. No inference.\n"
    "2. Skip session-level detail that won't matter in 30 days.\n"
    '3. Ephemeral TODOs ("follow up on X") are NOT claims. Skip them.\n'
    "4. If nothing in the digest is durable, return an empty array.\n"
    "5. Prefer the most specific subject_ref that applies."
)

_CLAIM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "subject_ref": {"type": "string"},
                    "kind": {"type": "string", "enum": ["fact", "preference", "state", "decision", "note"]},
                    "statement": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "time_ref": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["subject_ref", "kind", "statement", "confidence", "time_ref", "reason"],
            },
        }
    },
    "required": ["claims"],
}


@dataclass(frozen=True, slots=True)
class OpenRouterDigestExtractor:
    client: OpenRouterClient

    def extract(
        self,
        *,
        digest_text: str,
        digest_date: str | None,
        evidence_path: str,
    ) -> list[DigestClaim]:
        if not digest_text.strip():
            return []
        user_prompt = _build_user_prompt(digest_text=digest_text, digest_date=digest_date)
        try:
            payload = self.client.generate_json(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema_name="dory_digest_claims",
                schema=_CLAIM_SCHEMA,
            )
        except OpenRouterProviderError:
            return []
        return _parse_claims(payload, evidence_path=evidence_path, digest_date=digest_date)


def _build_user_prompt(*, digest_text: str, digest_date: str | None) -> str:
    header = f"Digest date: {digest_date}\n\n" if digest_date else ""
    return f"{header}Digest content:\n\n{digest_text}"


def _parse_claims(
    payload: Any,
    *,
    evidence_path: str,
    digest_date: str | None,
) -> list[DigestClaim]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("claims")
    if not isinstance(raw, list):
        return []
    claims: list[DigestClaim] = []
    for item in raw:
        claim = _coerce_claim(item, evidence_path=evidence_path, digest_date=digest_date)
        if claim is not None:
            claims.append(claim)
    return claims


def _coerce_claim(
    item: Any,
    *,
    evidence_path: str,
    digest_date: str | None,
) -> DigestClaim | None:
    if not isinstance(item, dict):
        return None
    subject_ref = item.get("subject_ref")
    kind = item.get("kind")
    statement = item.get("statement")
    confidence = item.get("confidence")
    if not isinstance(subject_ref, str) or ":" not in subject_ref:
        return None
    if kind not in _ALLOWED_KINDS:
        return None
    if not isinstance(statement, str) or not statement.strip():
        return None
    if confidence not in _ALLOWED_CONFIDENCE:
        return None
    time_ref = item.get("time_ref")
    if time_ref is not None and not isinstance(time_ref, str):
        time_ref = None
    if not time_ref:
        time_ref = digest_date
    reason = item.get("reason")
    if not isinstance(reason, str):
        reason = None
    return DigestClaim(
        subject_ref=subject_ref.strip(),
        kind=kind,  # type: ignore[arg-type]
        statement=statement.strip(),
        confidence=confidence,
        time_ref=time_ref,
        evidence_path=evidence_path,
        reason=reason.strip() if reason else None,
    )


@dataclass(frozen=True, slots=True)
class MiningResult:
    digest_path: str
    claims_extracted: int
    claims_stored: int
    skipped_claims: list[DigestClaim] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def mine_digest_file(
    digest_path: Path,
    *,
    corpus_root: Path,
    extractor: DigestExtractor,
    claim_store: ClaimStore | None = None,
    dry_run: bool = False,
) -> MiningResult:
    """Mine claims out of a single digest file.

    ``digest_path`` is the canonical corpus-relative path to the digest
    (e.g. ``logs/daily/2026-04-10.md``). ``corpus_root`` is the root that
    path is relative to. Returns a ``MiningResult`` with counts and any
    claims that couldn't be stored.
    """
    absolute = corpus_root / digest_path if not digest_path.is_absolute() else digest_path
    if not absolute.exists():
        return MiningResult(
            digest_path=str(digest_path),
            claims_extracted=0,
            claims_stored=0,
            errors=[f"digest file missing: {absolute}"],
        )

    text = absolute.read_text(encoding="utf-8")
    try:
        document = load_markdown_document(text)
        body = document.body
        digest_date = _extract_digest_date(document.frontmatter)
    except ValueError:
        body = text
        digest_date = None

    evidence_path = (
        digest_path.as_posix() if not digest_path.is_absolute() else (absolute.relative_to(corpus_root).as_posix())
    )

    claims = extractor.extract(
        digest_text=body,
        digest_date=digest_date,
        evidence_path=evidence_path,
    )

    if dry_run or claim_store is None:
        return MiningResult(
            digest_path=evidence_path,
            claims_extracted=len(claims),
            claims_stored=0,
            skipped_claims=list(claims) if dry_run else [],
        )

    stored = 0
    errors: list[str] = []
    for claim in claims:
        try:
            claim_store.add_claim(
                entity_id=claim.subject_ref,
                kind=claim.kind,
                statement=claim.statement,
                evidence_path=claim.evidence_path,
                confidence=claim.confidence,
                occurred_at=claim.time_ref,
            )
            stored += 1
        except Exception as err:  # pragma: no cover - defensive
            errors.append(f"{claim.subject_ref}: {type(err).__name__}: {err}")

    return MiningResult(
        digest_path=evidence_path,
        claims_extracted=len(claims),
        claims_stored=stored,
        errors=errors,
    )


def mine_digest_tree(
    corpus_root: Path,
    *,
    extractor: DigestExtractor,
    claim_store: ClaimStore | None = None,
    dry_run: bool = False,
    since: str | None = None,
    limit: int | None = None,
    include_weekly: bool = True,
) -> list[MiningResult]:
    """Mine every digest under ``corpus_root``.

    Scans ``logs/daily/``, ``digests/daily/``, ``logs/weekly/``, and
    ``digests/weekly/`` (when ``include_weekly`` is True). Filters by
    ``since`` (ISO date) if provided; limits to ``limit`` files if set.
    """
    roots = [
        corpus_root / "logs" / "daily",
        corpus_root / "digests" / "daily",
    ]
    if include_weekly:
        roots.append(corpus_root / "logs" / "weekly")
        roots.append(corpus_root / "digests" / "weekly")

    paths: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        paths.extend(sorted(root.rglob("*.md")))

    if since is not None:
        paths = [p for p in paths if _path_date_at_or_after(p, since)]

    if limit is not None:
        paths = paths[:limit]

    results: list[MiningResult] = []
    for path in paths:
        relative = path.relative_to(corpus_root)
        result = mine_digest_file(
            relative,
            corpus_root=corpus_root,
            extractor=extractor,
            claim_store=claim_store,
            dry_run=dry_run,
        )
        results.append(result)
    return results


def _extract_digest_date(frontmatter: dict[str, object]) -> str | None:
    for key in ("date", "created", "updated"):
        value = frontmatter.get(key)
        if isinstance(value, str) and len(value) >= 10:
            return value[:10]
    return None


def _path_date_at_or_after(path: Path, since: str) -> bool:
    stem = path.stem
    if len(stem) < 10:
        return True
    candidate = stem[:10]
    try:
        return candidate >= since
    except TypeError:
        return True


def format_mining_summary(results: Sequence[MiningResult]) -> dict[str, object]:
    total_files = len(results)
    total_extracted = sum(r.claims_extracted for r in results)
    total_stored = sum(r.claims_stored for r in results)
    files_with_claims = sum(1 for r in results if r.claims_extracted > 0)
    errors = sum(len(r.errors) for r in results)
    return {
        "total_files": total_files,
        "files_with_claims": files_with_claims,
        "total_claims_extracted": total_extracted,
        "total_claims_stored": total_stored,
        "total_errors": errors,
    }
