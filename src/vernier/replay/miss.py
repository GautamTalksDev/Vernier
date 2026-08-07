"""Typed miss when replay cannot find a recorded stub."""

from __future__ import annotations


class ReplayMiss(LookupError):
    """Raised when a live call has no matching recorded response.

    Never fabricate. Never fall through to the network or real tool
    (full-stub mode). In live-diff mode, tool misses become
    :class:`ReplayToolMiss` instead.
    """

    def __init__(self, channel: str, detail: str) -> None:
        self.channel = channel
        self.detail = detail
        super().__init__(f"ReplayMiss ({channel}): {detail}")


class ReplayToolMiss(RuntimeError):
    """Typed tool error returned to the agent when a tool stub is missing.

    The miss is recorded; the agent continues so divergence remains the finding.
    """

    def __init__(self, *, name: str, detail: str) -> None:
        self.name = name
        self.detail = detail
        super().__init__(f"ReplayToolMiss ({name}): {detail}")
