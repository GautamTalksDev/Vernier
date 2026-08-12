"""Load versioned alignment costs from ``costs-v1.toml``."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from vernier.align.types import AlignEvent, AlignKind

COSTS_RESOURCE: Final = "costs-v1.toml"
_PACKAGED_COSTS: Final = Path(__file__).with_name(COSTS_RESOURCE)


@dataclass(frozen=True, slots=True)
class AlignCosts:
    version: str
    max_events: int
    gap: int
    match: Mapping[str, int]
    mismatch_default: int
    mismatch_pairs: Mapping[tuple[str, str], int]

    def score_diagonal(self, a: AlignEvent, b: AlignEvent) -> tuple[int, bool]:
        """Return ``(score, is_match)`` for aligning ``a`` with ``b`` on a diagonal.

        ``is_match`` is True only for like-for-like correspondence (same kind,
        and for ToolCall the same tool name). Otherwise the pair is a
        substitution for relation labeling.
        """
        if a.kind is not b.kind:
            key = (a.kind.value, b.kind.value)
            score = self.mismatch_pairs.get(key, self.mismatch_default)
            return score, False

        if a.kind is AlignKind.TOOL_CALL:
            assert a.tool_name is not None and b.tool_name is not None
            if a.tool_name == b.tool_name:
                return self.match["ToolCall_same_name"], True
            return self.match["ToolCall_diff_name"], False

        return self.match[a.kind.value], True


def load_costs(path: Path | None = None) -> AlignCosts:
    """Load costs from ``path`` or the packaged ``costs-v1.toml``."""
    raw = Path(path).read_bytes() if path is not None else _PACKAGED_COSTS.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))

    match = {str(k): int(v) for k, v in dict(data["match"]).items()}
    required_match = {
        "LlmTurn",
        "ToolResult",
        "Terminal",
        "ToolCall_same_name",
        "ToolCall_diff_name",
    }
    missing = required_match - set(match)
    if missing:
        raise ValueError(f"costs file missing match keys: {sorted(missing)}")

    pairs_raw = dict(data.get("mismatch", {}).get("pairs", {}))
    pairs: dict[tuple[str, str], int] = {}
    for key, value in pairs_raw.items():
        left, sep, right = str(key).partition("__")
        if not sep:
            raise ValueError(f"mismatch.pairs key must be A__B, got {key!r}")
        pairs[(left, right)] = int(value)

    meta = dict(data.get("meta", {}))
    max_events = min(int(meta.get("max_events", 5000)), 5000)
    return AlignCosts(
        version=str(data["version"]),
        max_events=max_events,
        gap=int(data["gap"]["score"]),
        match=match,
        mismatch_default=int(data["mismatch"]["default"]),
        mismatch_pairs=pairs,
    )
