"""Trivial canonical package smoke test for CI."""

from vernier import canonical


def test_canonical_package_is_importable() -> None:
    assert canonical.__doc__ is not None
    assert "Canonicalization" in canonical.__doc__
