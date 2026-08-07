"""Vernier append-only record format ``vernier-record-v1``.

A run is an ordered sequence of typed events. ``seq`` is assigned at call
*initiation*, not completion, so in-flight and completed calls share one
monotonic timeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

RECORD_FORMAT_VERSION: Final = "vernier-record-v1"


class EventKind(StrEnum):
    """Typed event kinds in ``vernier-record-v1``."""

    RUN_START = "RunStart"
    LLM_REQUEST = "LlmRequest"
    LLM_RESPONSE = "LlmResponse"
    TOOL_CALL = "ToolCall"
    TOOL_RESULT = "ToolResult"
    TOOL_ERROR = "ToolError"
    ENV_READ = "EnvRead"
    WALL_CLOCK = "WallClock"
    RANDOM = "Random"
    REPLAY_MISS = "ReplayMiss"
    RUN_END = "RunEnd"
    CAPTURE_GAP = "CaptureGap"


@dataclass(frozen=True, slots=True)
class Event:
    """One typed event in a run.

    Attributes:
        run_id: Stable identifier for the enclosing run.
        seq: Monotonic sequence number assigned at call initiation.
        logical_parent_seq: Parent event ``seq``, or ``None`` for roots.
        kind: Event type discriminant.
        payload_digest: Canonical digest (``sha256:<hex>``) of the payload.
        blob_ref: Opaque reference to the full payload blob.
    """

    run_id: str
    seq: int
    logical_parent_seq: int | None
    kind: EventKind
    payload_digest: str
    blob_ref: str

    def __post_init__(self) -> None:
        if self.seq < 0:
            raise ValueError("seq must be >= 0")
        if self.logical_parent_seq is not None and self.logical_parent_seq < 0:
            raise ValueError("logical_parent_seq must be >= 0 when set")
        if not self.payload_digest.startswith("sha256:"):
            raise ValueError("payload_digest must be 'sha256:<hex>'")
        if not self.blob_ref:
            raise ValueError("blob_ref must be non-empty")


@dataclass(frozen=True, slots=True)
class Run:
    """Ordered sequence of events for one agent run."""

    format_version: Literal["vernier-record-v1"]
    run_id: str
    events: tuple[Event, ...]

    def __post_init__(self) -> None:
        if self.format_version != RECORD_FORMAT_VERSION:
            raise ValueError(
                f"unsupported record format: {self.format_version!r}; "
                f"expected {RECORD_FORMAT_VERSION!r}"
            )
        if not self.run_id:
            raise ValueError("run_id must be non-empty")
        seen: set[int] = set()
        for event in self.events:
            if event.run_id != self.run_id:
                raise ValueError(
                    f"event seq={event.seq} run_id mismatch: "
                    f"{event.run_id!r} != {self.run_id!r}"
                )
            if event.seq in seen:
                raise ValueError(f"duplicate seq in run: {event.seq}")
            seen.add(event.seq)
