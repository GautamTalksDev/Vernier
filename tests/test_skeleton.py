"""Skeleton smoke tests (no application logic yet)."""

from vernier import __version__


def test_version() -> None:
    assert __version__ == "0.1.0"
