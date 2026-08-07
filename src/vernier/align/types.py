"""Typed event alphabet and alignment result types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AlignKind(StrEnum):
    """Alphabet used for trajectory alignment."""

    LLM_TURN = "LlmTurn"
    TOOL_CALL = "ToolCall"
    TOOL_RESULT = "ToolResult"
    TERMINAL = "Terminal"


class Relation(StrEnum):
    """How one aligned step relates across the two runs."""

    MATCHED = "Matched"
    INSERTED = "Inserted"
    DELETED = "Deleted"
    SUBSTITUTED = "Substituted"


@dataclass(frozen=True, slots=True, order=True)
class AlignEvent:
    """One symbol in the alignment alphabet.

    ``tool_name`` is significant only for :attr:`AlignKind.TOOL_CALL`;
    it is ignored (and should be ``None``) for other kinds.
    """

    kind: AlignKind
    tool_name: str | None = None

    def __post_init__(self) -> None:
        if self.kind is AlignKind.TOOL_CALL:
            if not self.tool_name:
                raise ValueError("ToolCall events require a non-empty tool_name")
        elif self.tool_name is not None:
            raise ValueError(f"{self.kind.value} events must not set tool_name")


@dataclass(frozen=True, slots=True)
class AlignedPair:
    """One step in a global alignment.

    ``a_idx`` / ``b_idx`` are 0-based indexes into the source sequences,
    or ``None`` when the relation is Inserted / Deleted respectively.
    """

    a_idx: int | None
    b_idx: int | None
    relation: Relation
