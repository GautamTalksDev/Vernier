"""Schema-versioned JSON report (vernier-report-v1)."""

from __future__ import annotations

from typing import Any

from vernier.align.types import AlignedPair, Relation
from vernier.canonical._jcs import serialize_jcs
from vernier.classify import verdict_to_dict
from vernier.classify.types import TrajectoryEvent, Verdict
from vernier.report.escape import escape_untrusted

REPORT_SCHEMA_VERSION = "vernier-report-v1"


def build_report(
    *,
    source_run_id: str,
    candidate_run_id: str,
    verdict: Verdict,
    pairs: tuple[AlignedPair, ...] | list[AlignedPair],
    traj_a: list[TrajectoryEvent],
    traj_b: list[TrajectoryEvent],
) -> dict[str, Any]:
    """Build a machine-readable divergence report."""
    region = diverging_region(pairs)
    steps = []
    for pair in region:
        steps.append(
            {
                "relation": pair.relation.value,
                "a": _event_summary(traj_a, pair.a_idx),
                "b": _event_summary(traj_b, pair.b_idx),
            }
        )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "candidate_run_id": candidate_run_id,
        "verdict": verdict_to_dict(verdict),
        "alignment": {
            "length": len(pairs),
            "all_matched": all(p.relation is Relation.MATCHED for p in pairs)
            if pairs
            else True,
        },
        "diverging_region": steps,
        "applied_ignores": [
            {
                "mechanism": i.mechanism,
                "pointer": i.pointer,
                "side": i.side,
            }
            for i in verdict.applied_ignores
        ],
    }


def report_to_jcs(report: dict[str, Any]) -> bytes:
    return serialize_jcs(report) + b"\n"


def diverging_region(
    pairs: tuple[AlignedPair, ...] | list[AlignedPair],
    *,
    context: int = 1,
) -> list[AlignedPair]:
    """Return the contiguous span covering all non-Matched pairs (+ context)."""
    if not pairs:
        return []
    diverging_idxs = [
        i for i, p in enumerate(pairs) if p.relation is not Relation.MATCHED
    ]
    if not diverging_idxs:
        return []
    start = max(0, diverging_idxs[0] - context)
    end = min(len(pairs) - 1, diverging_idxs[-1] + context)
    return list(pairs[start : end + 1])


def _event_summary(
    traj: list[TrajectoryEvent], idx: int | None
) -> dict[str, Any] | None:
    if idx is None or idx < 0 or idx >= len(traj):
        return None
    ev = traj[idx]
    summary: dict[str, Any] = {"kind": ev.kind}
    if ev.tool_name:
        summary["tool_name"] = escape_untrusted(ev.tool_name)
    if ev.terminal_kind:
        summary["terminal_kind"] = ev.terminal_kind
    if ev.final_message:
        summary["final_message"] = escape_untrusted(ev.final_message)
    if ev.kind == "ToolResult":
        summary["is_error"] = ev.is_error
    if ev.arguments is not None:
        # Digests only — never dump raw untrusted blobs into the report body
        # without escaping; stringify safely.
        summary["arguments"] = {
            escape_untrusted(str(k)): escape_untrusted(str(v))
            for k, v in ev.arguments.items()
        }
    return summary
