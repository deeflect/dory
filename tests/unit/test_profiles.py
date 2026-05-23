from __future__ import annotations

import pytest

from dory_core.errors import DoryValidationError
from dory_core.profiles import ProfileRegistry


def test_profile_registry_describes_builtin_and_custom_profiles(tmp_path) -> None:
    (tmp_path / "profiles.yaml").write_text(
        """
profiles:
  brand:
    wake:
      sections:
        - profiles/brand/default.md
    retrieval:
      allow:
        - profiles/brand/**
      deny:
        - projects/dory/**
      sessions: never
      use_helper_context: false
""".strip(),
        encoding="utf-8",
    )

    profiles = ProfileRegistry(tmp_path).describe_profiles()

    names = [profile["name"] for profile in profiles]
    assert "coding" in names
    assert "brand" in names
    brand = next(profile for profile in profiles if profile["name"] == "brand")
    assert brand["source"] == "custom"
    assert brand["wake"]["sections"] == ["profiles/brand/default.md"]
    assert brand["retrieval"]["allow"] == ["profiles/brand/**"]
    assert brand["retrieval"]["deny"] == ["projects/dory/**"]
    assert brand["retrieval"]["sessions"] == "never"
    assert brand["retrieval"]["use_helper_context"] is False


def test_wake_profile_unknown_name_raises(tmp_path) -> None:
    registry = ProfileRegistry(tmp_path)

    with pytest.raises(DoryValidationError, match="Unknown wake profile"):
        registry.wake_profile("nonexistent")


def test_wake_profile_unknown_name_does_not_fallback_to_default(tmp_path) -> None:
    registry = ProfileRegistry(tmp_path)

    with pytest.raises(DoryValidationError):
        registry.wake_profile("bogus_profile_name")


def test_wake_profile_empty_name_uses_default(tmp_path) -> None:
    registry = ProfileRegistry(tmp_path)
    profile = registry.wake_profile("")

    assert profile.sections == ("core/user.md", "core/soul.md", "core/env.md", "core/active.md", "core/identity.md", "core/defaults.md")


def test_wake_profile_known_builtin_succeeds(tmp_path) -> None:
    registry = ProfileRegistry(tmp_path)

    for name in ("default", "coding", "writing", "privacy", "casual"):
        profile = registry.wake_profile(name)
        assert len(profile.sections) > 0


def test_retrieval_profile_unknown_name_raises(tmp_path) -> None:
    registry = ProfileRegistry(tmp_path)

    with pytest.raises(DoryValidationError, match="Unknown retrieval profile"):
        registry.retrieval_profile("nonexistent")


def test_retrieval_profile_unknown_name_does_not_fallback_to_general(tmp_path) -> None:
    registry = ProfileRegistry(tmp_path)

    with pytest.raises(DoryValidationError):
        registry.retrieval_profile("bogus_profile_name")


def test_retrieval_profile_empty_name_uses_general(tmp_path) -> None:
    registry = ProfileRegistry(tmp_path)
    profile = registry.retrieval_profile("")

    assert profile.wake_profile == "default"
    assert profile.sessions == "intent_only"


def test_retrieval_profile_known_builtin_succeeds(tmp_path) -> None:
    registry = ProfileRegistry(tmp_path)

    for name in ("general", "coding", "writing", "privacy", "personal"):
        profile = registry.retrieval_profile(name)
        assert profile.wake_profile is not None


def test_custom_profile_from_yaml_wake_works(tmp_path) -> None:
    (tmp_path / "profiles.yaml").write_text(
        """
profiles:
  my_custom:
    wake:
      sections:
        - core/active.md
    retrieval:
      allow:
        - core/active.md
""".strip(),
        encoding="utf-8",
    )

    registry = ProfileRegistry(tmp_path)
    profile = registry.wake_profile("my_custom")
    assert profile.sections == ("core/active.md",)


def test_custom_profile_from_yaml_retrieval_works(tmp_path) -> None:
    (tmp_path / "profiles.yaml").write_text(
        """
profiles:
  my_custom:
    wake:
      sections:
        - core/active.md
    retrieval:
      allow:
        - core/active.md
""".strip(),
        encoding="utf-8",
    )

    registry = ProfileRegistry(tmp_path)
    profile = registry.retrieval_profile("my_custom")
    assert profile.wake_profile == "my_custom"
    assert profile.allow == ("core/active.md",)
