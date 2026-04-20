from __future__ import annotations

from dory_core.entity_registry import EntityRegistry


def test_entity_registry_resolves_alias_and_title_without_restart(tmp_path) -> None:
    registry = EntityRegistry(tmp_path / "entities.db")
    registry.upsert(
        entity_id="person:avery-z",
        family="person",
        title="Avery Z",
        target_path="people/avery-z.md",
        aliases=("avery",),
    )

    alias_match = registry.resolve("avery", family="person")
    title_match = registry.resolve("Avery Z", family="person")

    assert alias_match is not None
    assert alias_match.entity_id == "person:avery-z"
    assert alias_match.target_path == "people/avery-z.md"
    assert alias_match.matched_by == "alias"
    assert title_match is not None
    assert title_match.entity_id == "person:avery-z"
    assert title_match.matched_by == "title"


def test_entity_registry_merge_preserves_loser_aliases(tmp_path) -> None:
    registry = EntityRegistry(tmp_path / "entities.db")
    registry.upsert(
        entity_id="person:casey",
        family="person",
        title="Casey",
        target_path="people/casey.md",
        aliases=("jordan",),
    )
    registry.upsert(
        entity_id="person:jordan-example",
        family="person",
        title="Jordan Example",
        target_path="people/jordan-example.md",
        aliases=("casey",),
    )

    registry.merge("person:casey", "person:jordan-example")

    match = registry.resolve("Jordan Example", family="person")
    assert match is not None
    assert match.entity_id == "person:casey"
