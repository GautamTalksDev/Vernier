"""Human-readable side-by-side divergence report."""

from __future__ import annotations

import sys
from typing import TextIO

from vernier.align.types import AlignedPair, Relation
from vernier.classify.types import TrajectoryEvent, Verdict
from vernier.report.escape import escape_untrusted
from vernier.report.json_report import diverging_region

_COLOR = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
}


def use_color(stream: TextIO | None = None) -> bool:
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def render_human(
    *,
    source_run_id: str,
    candidate_run_id: str,
    verdict: Verdict,
    pairs: tuple[AlignedPair, ...] | list[AlignedPair],
    traj_a: list[TrajectoryEvent],
    traj_b: list[TrajectoryEvent],
    color: bool | None = None,
) -> str:
    """Render a side-by-side view of the diverging region."""
    if color is None:
        color = use_color()
    c = _COLOR if color else {k: "" for k in _COLOR}

    lines: list[str] = []
    sev = verdict.severity.value
    sev_color = {
        "NONE": c["green"],
        "LOW": c["cyan"],
        "MEDIUM": c["yellow"],
        "HIGH": c["red"],
        "CRITICAL": c["red"],
    }.get(sev, "")

    lines.append(f"{c['bold']}vern diff{c['reset']}")
    lines.append(f"  source    {escape_untrusted(source_run_id)}")
    lines.append(f"  candidate {escape_untrusted(candidate_run_id)}")
    lines.append(f"  severity  {sev_color}{sev}{c['reset']}")
    if verdict.rule_ids:
        rules = ", ".join(escape_untrusted(r) for r in verdict.rule_ids)
    else:
        rules = "(none)"
    lines.append(f"  rules     {rules}")
    if verdict.harness_fault:
        lines.append(
            f"  {c['red']}HARNESS_FAULT{c['reset']}  "
            f"{escape_untrusted(verdict.harness_fault)}"
        )

    if verdict.applied_ignores:
        lines.append("  ignores")
        for ign in verdict.applied_ignores:
            lines.append(
                f"    - {escape_untrusted(ign.mechanism)} "
                f"{escape_untrusted(ign.pointer)} ({escape_untrusted(ign.side)})"
            )
    else:
        lines.append("  ignores   (none)")

    region = diverging_region(pairs)
    lines.append("")
    if not region:
        lines.append(f"{c['green']}No diverging steps (all Matched).{c['reset']}")
    else:
        lines.append(f"{c['bold']}Diverging region{c['reset']}")
        lines.append(f"{'rel':<12} {'A':<36} {'B':<36}")
        lines.append("-" * 84)
        for pair in region:
            rel = pair.relation.value
            rel_c = {
                Relation.MATCHED.value: c["dim"],
                Relation.INSERTED.value: c["yellow"],
                Relation.DELETED.value: c["yellow"],
                Relation.SUBSTITUTED.value: c["red"],
            }.get(rel, "")
            a_s = _fmt_event(traj_a, pair.a_idx)
            b_s = _fmt_event(traj_b, pair.b_idx)
            lines.append(f"{rel_c}{rel:<12}{c['reset']} {a_s:<36} {b_s:<36}")

    if verdict.fired:
        lines.append("")
        lines.append(f"{c['bold']}Fired rules{c['reset']}")
        for rule in verdict.fired:
            detail = escape_untrusted(rule.detail)
            lines.append(f"  {rule.id} ({rule.severity.value}): {detail}")

    return "\n".join(lines) + "\n"


def _fmt_event(traj: list[TrajectoryEvent], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(traj):
        return "—"
    ev = traj[idx]
    if ev.kind == "ToolCall":
        name = escape_untrusted(ev.tool_name or "?")
        return f"ToolCall:{name}"[:36]
    if ev.kind == "ToolResult":
        return "ToolResult:error" if ev.is_error else "ToolResult:ok"
    if ev.kind == "Terminal":
        return f"Terminal:{escape_untrusted(ev.terminal_kind or '?')}"[:36]
    return escape_untrusted(ev.kind)[:36]
