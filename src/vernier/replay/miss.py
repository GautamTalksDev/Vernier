"""Typed miss when replay cannot find a recorded stub."""

from __future__ import annotations


class ReplayMiss(LookupError):
    """Raised when a live call has no matching recorded response.

    Never fabricate. Never fall through to the network or real tool.
    """

    def __init__(self, channel: str, detail: str) -> None:
        self.channel = channel
        self.detail = detail
        super().__init__(f"ReplayMiss ({channel}): {detail}")
