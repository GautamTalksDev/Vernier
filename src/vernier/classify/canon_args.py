"""Argument equality after ignore-list (D04 / action sequence)."""

from __future__ import annotations

from typing import Any

from vernier.canonical._jcs import serialize_jcs
from vernier.classify.ignore import apply_ignores, merge_applied_ignores
from vernier.classify.types import AppliedIgnore, IgnoreConfig


def scrub_and_jcs(
    arguments: dict[str, Any] | None,
    config: IgnoreConfig,
    *,
    side: str,
) -> tuple[bytes, list[AppliedIgnore]]:
    scrubbed, ignores = apply_ignores(arguments, config, side=side)
    if scrubbed is None:
        return b"null", ignores
    return serialize_jcs(scrubbed), ignores


def arguments_equal(
    a_args: dict[str, Any] | None,
    b_args: dict[str, Any] | None,
    config: IgnoreConfig,
) -> tuple[bool, list[AppliedIgnore]]:
    a_bytes, a_ign = scrub_and_jcs(a_args, config, side="a")
    b_bytes, b_ign = scrub_and_jcs(b_args, config, side="b")
    return a_bytes == b_bytes, merge_applied_ignores(a_ign, b_ign)
