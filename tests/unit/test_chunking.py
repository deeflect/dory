from __future__ import annotations

from dory_core.chunking import chunk_markdown


def test_chunk_markdown_preserves_frontmatter_in_chunk_zero() -> None:
    text = """---
title: User
type: core
---

hello world
"""

    chunks = chunk_markdown(text, max_tokens=800)

    assert len(chunks) == 1
    assert chunks[0].content.startswith("---")


def test_chunk_markdown_splits_large_body_into_multiple_chunks() -> None:
    text = """---
title: User
type: core
---

# One

alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu

# Two

nu xi omicron pi rho sigma tau upsilon phi chi psi omega
"""

    chunks = chunk_markdown(text, max_tokens=6)

    assert len(chunks) >= 2
    assert chunks[0].content.startswith("---")
