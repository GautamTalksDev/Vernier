"""Types for divergence-v1 classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


_SEVERITY_RANK = {
    Severity.NONE: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def severity_rank(level: Severity) -> int:
    return _SEVERITY_RANK[level]


def max_severity(levels: list[Severity]) -> Severity:
    if not levels:
        return Severity.NONE
    return max(levels, key=severity_rank)


class TerminalKind(StrEnum):
    FINAL_MESSAGE = "FinalMessage"
    TOOL_CALL = "ToolCall"


@dataclass(frozen=True, slots=True)
class TrajectoryEvent:
    """Rich trajectory event (SPEC-DIVERGENCE §2.1)."""

    kind: str
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None
    terminal_kind: str | None = None
    final_message: str | None = None
    is_error: bool = False
    destructive: bool = False
    parallel_group: str | None = None
    retry_index: int = 0


@dataclass(frozen=True, slots=True)
class AppliedIgnore:
    mechanism: str
    pointer: str
    side: str


@dataclass(frozen=True, slots=True)
class FiredRule:
    id: str
    severity: Severity
    detail: str


@dataclass(frozen=True, slots=True)
class Verdict:
    spec_version: str
    severity: Severity
    rule_ids: tuple[str, ...]
    fired: tuple[FiredRule, ...]
    applied_ignores: tuple[AppliedIgnore, ...]
    # Instrument failure — never a D-rule behavioural finding.
    # When set, severity is NONE and rule_ids/fired are empty.
    harness_fault: str | None = None


HARNESS_FAULT_ZERO_TOOL_COLLAPSE = (
    "ZERO_TOOL_COLLAPSE: candidate produced zero ToolCall events while "
    "baseline produced one or more. This is a harness/setup fault, not a "
    "divergence-v1 behavioural finding."
)

HARNESS_FAULT_REQUEST_BODY_MISMATCH = (
    "REQUEST_BODY_MISMATCH: candidate LLM request body differs from baseline "
    "in a control field outside allowed overrides (model/seed). This is a "
    "harness defect, not a divergence-v1 behavioural finding."
)


@dataclass
class IgnoreConfig:
    version: str = "divergence-v1"
    pointers: list[str] = field(default_factory=list)
    strip_uuid_values: bool = True
    strip_iso_timestamps: bool = True
