from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


from dory_core.claim_store import ClaimStore
from dory_core.digest_mining import (
    DigestClaim,
    OpenRouterDigestExtractor,
    _coerce_claim,
    _parse_claims,
    format_mining_summary,
    mine_digest_file,
    mine_digest_tree,
)
from dory_core.llm.openrouter import OpenRouterProviderError


@dataclass
class _FakeClient:
    payload: Any = None
    raise_error: bool = False
    captured_prompts: list[str] = field(default_factory=list)

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict,
    ) -> Any:
        self.captured_prompts.append(user_prompt)
        if self.raise_error:
            raise OpenRouterProviderError("boom")
        return self.payload


@dataclass
class _StubExtractor:
    claims: list[DigestClaim]

    def extract(self, *, digest_text, digest_date, evidence_path):
        return list(self.claims)


def _write_digest(corpus_root: Path, relative: str, content: str) -> Path:
    path = corpus_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_extractor_parses_valid_payload() -> None:
    client = _FakeClient(
        payload={
            "claims": [
                {
                    "subject_ref": "project:rooster-spec",
                    "kind": "state",
                    "statement": "Rooster moved from spec to build",
                    "confidence": "high",
                    "time_ref": "2026-04-10",
                    "reason": "Shipped 3-tab UX",
                },
                {
                    "subject_ref": "core:soul",
                    "kind": "preference",
                    "statement": "Casey prefers ADHD-friendly minimal UI",
                    "confidence": "medium",
                    "time_ref": "2026-04-10",
                    "reason": "Stated during dashboard review",
                },
            ]
        }
    )
    extractor = OpenRouterDigestExtractor(client=client)  # type: ignore[arg-type]

    claims = extractor.extract(
        digest_text="some digest",
        digest_date="2026-04-10",
        evidence_path="logs/daily/2026-04-10.md",
    )

    assert len(claims) == 2
    assert claims[0].subject_ref == "project:rooster-spec"
    assert claims[0].kind == "state"
    assert claims[0].evidence_path == "logs/daily/2026-04-10.md"
    assert claims[1].kind == "preference"


def test_extractor_returns_empty_on_provider_error() -> None:
    client = _FakeClient(raise_error=True)
    extractor = OpenRouterDigestExtractor(client=client)  # type: ignore[arg-type]

    assert extractor.extract(digest_text="x", digest_date=None, evidence_path="p") == []


def test_extractor_returns_empty_for_empty_digest() -> None:
    client = _FakeClient(payload={"claims": []})
    extractor = OpenRouterDigestExtractor(client=client)  # type: ignore[arg-type]

    claims = extractor.extract(digest_text="   \n  ", digest_date=None, evidence_path="p")

    assert claims == []
    assert client.captured_prompts == []


def test_parse_claims_drops_malformed_entries() -> None:
    payload = {
        "claims": [
            {
                "subject_ref": "project:alpha",
                "kind": "state",
                "statement": "ok",
                "confidence": "high",
                "time_ref": None,
                "reason": None,
            },
            {
                "subject_ref": "no-colon",
                "kind": "state",
                "statement": "x",
                "confidence": "high",
                "time_ref": None,
                "reason": None,
            },
            {
                "subject_ref": "project:alpha",
                "kind": "bogus",
                "statement": "x",
                "confidence": "high",
                "time_ref": None,
                "reason": None,
            },
            {
                "subject_ref": "project:alpha",
                "kind": "state",
                "statement": "",
                "confidence": "high",
                "time_ref": None,
                "reason": None,
            },
            {
                "subject_ref": "project:alpha",
                "kind": "state",
                "statement": "ok",
                "confidence": "bogus",
                "time_ref": None,
                "reason": None,
            },
        ]
    }

    claims = _parse_claims(payload, evidence_path="p", digest_date=None)

    assert len(claims) == 1
    assert claims[0].subject_ref == "project:alpha"


def test_parse_claims_inherits_digest_date_when_time_ref_missing() -> None:
    claim = _coerce_claim(
        {
            "subject_ref": "core:soul",
            "kind": "preference",
            "statement": "x",
            "confidence": "high",
            "time_ref": None,
            "reason": None,
        },
        evidence_path="logs/daily/2026-04-10.md",
        digest_date="2026-04-10",
    )

    assert claim is not None
    assert claim.time_ref == "2026-04-10"


def test_mine_digest_file_stores_claims(tmp_path: Path) -> None:
    corpus = tmp_path
    _write_digest(
        corpus,
        "logs/daily/2026-04-10.md",
        "---\ntitle: digest\ntype: daily\ndate: 2026-04-10\n---\n\nBody\n",
    )

    extractor = _StubExtractor(
        claims=[
            DigestClaim(
                subject_ref="project:clawsy",
                kind="state",
                statement="Clawsy is active focus",
                confidence="high",
                time_ref="2026-04-10",
                evidence_path="logs/daily/2026-04-10.md",
                reason=None,
            )
        ]
    )
    store = ClaimStore(tmp_path / "claims.db")

    result = mine_digest_file(
        Path("logs/daily/2026-04-10.md"),
        corpus_root=corpus,
        extractor=extractor,
        claim_store=store,
    )

    assert result.claims_extracted == 1
    assert result.claims_stored == 1
    claims = store.current_claims("project:clawsy")
    assert len(claims) == 1
    assert claims[0].statement == "Clawsy is active focus"


def test_mine_digest_file_dry_run_does_not_store(tmp_path: Path) -> None:
    corpus = tmp_path
    _write_digest(corpus, "logs/daily/2026-04-10.md", "---\ntype: daily\ndate: 2026-04-10\ntitle: x\n---\n\nBody\n")
    extractor = _StubExtractor(
        claims=[
            DigestClaim(
                subject_ref="core:soul",
                kind="preference",
                statement="x",
                confidence="high",
                time_ref=None,
                evidence_path="logs/daily/2026-04-10.md",
                reason=None,
            )
        ]
    )
    store = ClaimStore(tmp_path / "claims.db")

    result = mine_digest_file(
        Path("logs/daily/2026-04-10.md"),
        corpus_root=corpus,
        extractor=extractor,
        claim_store=store,
        dry_run=True,
    )

    assert result.claims_extracted == 1
    assert result.claims_stored == 0
    assert len(result.skipped_claims) == 1
    assert store.current_claims("core:soul") == ()


def test_mine_digest_tree_filters_since_and_limit(tmp_path: Path) -> None:
    corpus = tmp_path
    _write_digest(corpus, "logs/daily/2026-02-01.md", "---\ntitle: a\ntype: daily\ndate: 2026-02-01\n---\n\nb\n")
    _write_digest(corpus, "logs/daily/2026-03-15.md", "---\ntitle: b\ntype: daily\ndate: 2026-03-15\n---\n\nb\n")
    _write_digest(corpus, "logs/daily/2026-04-10.md", "---\ntitle: c\ntype: daily\ndate: 2026-04-10\n---\n\nb\n")
    extractor = _StubExtractor(claims=[])

    results = mine_digest_tree(corpus, extractor=extractor, since="2026-03-01", dry_run=True)
    dates = [Path(r.digest_path).stem for r in results]
    assert "2026-02-01" not in dates
    assert "2026-03-15" in dates
    assert "2026-04-10" in dates

    limited = mine_digest_tree(corpus, extractor=extractor, limit=1, dry_run=True)
    assert len(limited) == 1


def test_format_mining_summary_aggregates() -> None:
    # Build results manually to avoid filesystem churn.
    from dory_core.digest_mining import MiningResult

    batch = [
        MiningResult(digest_path="a.md", claims_extracted=3, claims_stored=3),
        MiningResult(digest_path="b.md", claims_extracted=0, claims_stored=0),
        MiningResult(digest_path="c.md", claims_extracted=2, claims_stored=1, errors=["oops"]),
    ]

    summary = format_mining_summary(batch)

    assert summary == {
        "total_files": 3,
        "files_with_claims": 2,
        "total_claims_extracted": 5,
        "total_claims_stored": 4,
        "total_errors": 1,
    }
