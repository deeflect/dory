from __future__ import annotations

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
