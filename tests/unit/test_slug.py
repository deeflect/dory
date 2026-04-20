from __future__ import annotations

from dory_core.slug import slugify_path_segment


def test_slugify_drops_accents_and_punctuation() -> None:
    assert slugify_path_segment("Sarah K.") == "sarah-k"
