"""Harness-fault detection: instrument failures that must not look like D-rules."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from vernier.classify.types import (
    HARNESS_FAULT_REQUEST_BODY_MISMATCH,
    HARNESS_FAULT_ZERO_TOOL_COLLAPSE,
    Severity,
    TrajectoryEvent,
    Verdict,
)
from vernier.record.store import RecordStore, load_run

_TRANSPORT_MARKERS = (
    "replay.invalid",
    "ConnectError",
    "Name or service not known",
    "Connection refused",
    "nodename nor servname",
    "Failed to establish a new connection",
)


class _RedriveLike(Protocol):
    traj_a: list[TrajectoryEvent]
    traj_b: list[TrajectoryEvent]
    verdict: Verdict
    agent_exit: int
    agent_output: str
    candidate_dir: Path


def tool_call_count(traj: list[TrajectoryEvent]) -> int:
    return sum(1 for e in traj if e.kind == "ToolCall")


def agent_output_suggests_transport_failure(agent_output: str) -> bool:
    text = agent_output or ""
    return any(m in text for m in _TRANSPORT_MARKERS)


def request_body_mismatch_fault(candidate_dir: Path) -> str | None:
    """Return harness fault text when live-diff aborted on request parity."""
    if not candidate_dir.is_dir():
        return None
    try:
        frozen = load_run(candidate_dir)
    except (OSError, ValueError):
        return None
    reader = RecordStore(candidate_dir, frozen.run_id)
    for env in reader.read_events():
        if env["kind"] != "ReplayMiss":
            continue
        payload = reader.read_payload(env)
        if str(payload.get("channel") or "") != "harness":
            continue
        detail = str(payload.get("detail") or "")
        if "REQUEST_BODY_MISMATCH" in detail:
            return (
                detail
                if detail.startswith(HARNESS_FAULT_REQUEST_BODY_MISMATCH.split(":")[0])
                else (f"{HARNESS_FAULT_REQUEST_BODY_MISMATCH} {detail}")
            )
    output_path = candidate_dir / "agent_output.txt"
    if output_path.is_file():
        text = output_path.read_text(encoding="utf-8", errors="replace")
        if "REQUEST_BODY_MISMATCH" in text:
            return HARNESS_FAULT_REQUEST_BODY_MISMATCH
    return None


def apply_harness_guard(result: _RedriveLike) -> Verdict:
    """Return verdict with harness_fault set when the instrument collapsed.

    Criterion (fail closed, fail loudly):
      - baseline ToolCall count > 0
      - candidate ToolCall count == 0
      - AND evidence the agent did not complete a deliberate skip:
          agent_exit != 0, or transport/DNS failure markers in agent output.

    Intentional zero-tool skips (exit 0, no transport failure) keep D-rule
    classification (typically D05).
    """
    parity_fault = request_body_mismatch_fault(result.candidate_dir)
    if parity_fault:
        return Verdict(
            spec_version=result.verdict.spec_version,
            severity=Severity.NONE,
            rule_ids=(),
            fired=(),
            applied_ignores=(),
            harness_fault=parity_fault,
        )

    body_fault = None
    if "REQUEST_BODY_MISMATCH" in (result.agent_output or ""):
        body_fault = HARNESS_FAULT_REQUEST_BODY_MISMATCH
    if body_fault:
        return Verdict(
            spec_version=result.verdict.spec_version,
            severity=Severity.NONE,
            rule_ids=(),
            fired=(),
            applied_ignores=(),
            harness_fault=body_fault,
        )

    verdict = result.verdict
    a_tools = tool_call_count(result.traj_a)
    b_tools = tool_call_count(result.traj_b)
    if a_tools > 0 and b_tools == 0:
        crashed = result.agent_exit != 0
        transport = agent_output_suggests_transport_failure(result.agent_output)
        if crashed or transport:
            return Verdict(
                spec_version=verdict.spec_version,
                severity=Severity.NONE,
                rule_ids=(),
                fired=(),
                applied_ignores=(),
                harness_fault=HARNESS_FAULT_ZERO_TOOL_COLLAPSE,
            )
    return verdict
