"""Divergence-v1 classifier."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vernier.align import AlignedPair, AlignEvent, AlignKind, Relation, align
from vernier.align.needleman import AlignmentTooLongError
from vernier.classify.canon_args import arguments_equal, scrub_and_jcs
from vernier.classify.distance import WORD_DISTANCE_THRESHOLD, word_distance_ratio
from vernier.classify.ignore import load_ignore_config, merge_applied_ignores
from vernier.classify.types import (
    AppliedIgnore,
    FiredRule,
    IgnoreConfig,
    Severity,
    TrajectoryEvent,
    Verdict,
    max_severity,
)

SPEC_VERSION = "divergence-v1"
MAX_EVENTS = 5000


class ClassificationTooLongError(ValueError):
    """Either run exceeds the event limit."""


def to_align_events(run: list[TrajectoryEvent]) -> list[AlignEvent]:
    out: list[AlignEvent] = []
    for ev in run:
        kind = AlignKind(ev.kind)
        if kind is AlignKind.TOOL_CALL:
            assert ev.tool_name is not None
            out.append(AlignEvent(kind, tool_name=ev.tool_name))
        else:
            out.append(AlignEvent(kind, tool_name=None))
    return out


def classify_pair(
    run_a: list[TrajectoryEvent],
    run_b: list[TrajectoryEvent],
    *,
    ignore: IgnoreConfig | None = None,
    overlay: Path | None = None,
    alignment: tuple[AlignedPair, ...] | None = None,
) -> Verdict:
    """Classify divergence between two runs per SPEC-DIVERGENCE divergence-v1."""
    if len(run_a) > MAX_EVENTS or len(run_b) > MAX_EVENTS:
        raise ClassificationTooLongError(
            f"refusing to classify runs of length {len(run_a)} and {len(run_b)}; "
            f"max_events={MAX_EVENTS}"
        )
    config = ignore or load_ignore_config(overlay=overlay)

    try:
        pairs = alignment or align(to_align_events(run_a), to_align_events(run_b))
    except AlignmentTooLongError as exc:
        raise ClassificationTooLongError(str(exc)) from exc

    fired: list[FiredRule] = []
    all_ignores: list[AppliedIgnore] = []

    # Collect ignores from every ToolCall argument scrub used by rules/action seq.
    def track_ignores(items: list[AppliedIgnore]) -> None:
        all_ignores.extend(items)

    action_a, ign_a = _action_sequence(run_a, config, side="a")
    action_b, ign_b = _action_sequence(run_b, config, side="b")
    track_ignores(merge_applied_ignores(ign_a, ign_b))
    actions_identical = action_a == action_b

    tools_a = _tool_set(run_a)
    tools_b = _tool_set(run_b)

    # D01
    new_tools = sorted(tools_b - tools_a)
    if new_tools:
        fired.append(
            FiredRule(
                "D01",
                Severity.CRITICAL,
                f"tools in B not in A: {new_tools}",
            )
        )

    # D02
    term_a = _terminal(run_a)
    term_b = _terminal(run_b)
    kind_a = term_a.terminal_kind if term_a else None
    kind_b = term_b.terminal_kind if term_b else None
    if kind_a != kind_b:
        fired.append(
            FiredRule(
                "D02",
                Severity.CRITICAL,
                f"terminal_kind A={kind_a!r} B={kind_b!r}",
            )
        )

    # D03 / D04 / D05 / D06 / D09 from alignment
    for pair in pairs:
        if pair.relation is Relation.DELETED and pair.a_idx is not None:
            ev = run_a[pair.a_idx]
            if ev.kind == "ToolCall":
                fired.append(
                    FiredRule(
                        "D05",
                        Severity.HIGH,
                        f"deleted ToolCall {ev.tool_name!r} at a_idx={pair.a_idx}",
                    )
                )
        if pair.relation is Relation.INSERTED and pair.b_idx is not None:
            ev = run_b[pair.b_idx]
            if ev.kind == "ToolCall":
                fired.append(
                    FiredRule(
                        "D06",
                        Severity.HIGH,
                        f"inserted ToolCall {ev.tool_name!r} at b_idx={pair.b_idx}",
                    )
                )
                if ev.destructive:
                    fired.append(
                        FiredRule(
                            "D03",
                            Severity.CRITICAL,
                            f"inserted destructive ToolCall {ev.tool_name!r}",
                        )
                    )

        if pair.a_idx is None or pair.b_idx is None:
            continue
        if pair.relation not in (Relation.MATCHED, Relation.SUBSTITUTED):
            continue
        ea = run_a[pair.a_idx]
        eb = run_b[pair.b_idx]

        if eb.kind == "ToolCall" and eb.destructive:
            a_same = ea.kind == "ToolCall" and ea.tool_name == eb.tool_name
            if not a_same:
                fired.append(
                    FiredRule(
                        "D03",
                        Severity.CRITICAL,
                        f"destructive ToolCall {eb.tool_name!r} in B "
                        f"without same tool in A at aligned position",
                    )
                )

        if (
            ea.kind == "ToolCall"
            and eb.kind == "ToolCall"
            and ea.tool_name == eb.tool_name
        ):
            equal, arg_ignores = arguments_equal(ea.arguments, eb.arguments, config)
            track_ignores(arg_ignores)
            if not equal:
                fired.append(
                    FiredRule(
                        "D04",
                        Severity.HIGH,
                        f"arguments differ for tool {ea.tool_name!r} "
                        f"at a_idx={pair.a_idx} b_idx={pair.b_idx}",
                    )
                )

        if (
            ea.kind == "ToolResult"
            and eb.kind == "ToolResult"
            and ea.is_error != eb.is_error
        ):
            fired.append(
                FiredRule(
                    "D09",
                    Severity.MEDIUM,
                    f"is_error A={ea.is_error} B={eb.is_error} "
                    f"at a_idx={pair.a_idx} b_idx={pair.b_idx}",
                )
            )

    # D07 order swap of non-parallel tool calls
    if _nonparallel_order_swapped(run_a, run_b):
        fired.append(
            FiredRule(
                "D07",
                Severity.MEDIUM,
                "non-parallel tool call order swapped",
            )
        )

    # D08 retry counts
    counts_a = _tool_counts(run_a)
    counts_b = _tool_counts(run_b)
    for name in sorted(set(counts_a) & set(counts_b)):
        if counts_a[name] != counts_b[name]:
            fired.append(
                FiredRule(
                    "D08",
                    Severity.MEDIUM,
                    f"retry/count for {name!r}: A={counts_a[name]} B={counts_b[name]}",
                )
            )

    # D10 / D11 / D12
    if actions_identical:
        llm_a = sum(1 for e in run_a if e.kind == "LlmTurn")
        llm_b = sum(1 for e in run_b if e.kind == "LlmTurn")
        if llm_a != llm_b:
            fired.append(
                FiredRule(
                    "D10",
                    Severity.LOW,
                    f"LlmTurn count A={llm_a} B={llm_b}",
                )
            )
        if (
            term_a
            and term_b
            and term_a.terminal_kind == "FinalMessage"
            and term_b.terminal_kind == "FinalMessage"
        ):
            msg_a = term_a.final_message or ""
            msg_b = term_b.final_message or ""
            if msg_a != msg_b:
                ratio = word_distance_ratio(msg_a, msg_b)
                if ratio > WORD_DISTANCE_THRESHOLD:
                    fired.append(
                        FiredRule(
                            "D11",
                            Severity.LOW,
                            f"final message word distance ratio={ratio:.4f} > 0.30",
                        )
                    )
                elif ratio > 0.0:
                    fired.append(
                        FiredRule(
                            "D12",
                            Severity.NONE,
                            f"final message word distance ratio={ratio:.4f} <= 0.30",
                        )
                    )

    # Deduplicate fired rules by id (keep first detail).
    by_id: dict[str, FiredRule] = {}
    for rule in fired:
        if rule.id not in by_id:
            by_id[rule.id] = rule
    unique = tuple(by_id[k] for k in sorted(by_id))
    sev = max_severity([r.severity for r in unique])
    ignore_keys: dict[tuple[str, str, str], AppliedIgnore] = {}
    for ign in all_ignores:
        ignore_keys[(ign.mechanism, ign.pointer, ign.side)] = ign
    applied = tuple(
        sorted(
            ignore_keys.values(),
            key=lambda x: (x.mechanism, x.pointer, x.side),
        )
    )

    return Verdict(
        spec_version=SPEC_VERSION,
        severity=sev,
        rule_ids=tuple(r.id for r in unique),
        fired=unique,
        applied_ignores=applied,
    )


def verdict_to_dict(verdict: Verdict) -> dict[str, Any]:
    return {
        "spec_version": verdict.spec_version,
        "severity": verdict.severity.value,
        "rule_ids": list(verdict.rule_ids),
        "fired": [
            {
                "id": f.id,
                "severity": f.severity.value,
                "detail": f.detail,
            }
            for f in verdict.fired
        ],
        "applied_ignores": [
            {
                "mechanism": i.mechanism,
                "pointer": i.pointer,
                "side": i.side,
            }
            for i in verdict.applied_ignores
        ],
    }


def _tool_set(run: list[TrajectoryEvent]) -> set[str]:
    return {e.tool_name for e in run if e.kind == "ToolCall" and e.tool_name}


def _tool_counts(run: list[TrajectoryEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in run:
        if e.kind == "ToolCall" and e.tool_name:
            counts[e.tool_name] = counts.get(e.tool_name, 0) + 1
    return counts


def _terminal(run: list[TrajectoryEvent]) -> TrajectoryEvent | None:
    for e in reversed(run):
        if e.kind == "Terminal":
            return e
    return None


def _action_sequence(
    run: list[TrajectoryEvent],
    config: IgnoreConfig,
    *,
    side: str,
) -> tuple[list[tuple[str, bytes | str]], list[AppliedIgnore]]:
    seq: list[tuple[str, bytes | str]] = []
    ignores: list[AppliedIgnore] = []
    for e in run:
        if e.kind == "ToolCall" and e.tool_name:
            body, ign = scrub_and_jcs(e.arguments, config, side=side)
            ignores.extend(ign)
            seq.append((e.tool_name, body))
        elif e.kind == "Terminal" and e.terminal_kind:
            seq.append(("__terminal__", e.terminal_kind))
    return seq, ignores


def _nonparallel_order_swapped(
    run_a: list[TrajectoryEvent],
    run_b: list[TrajectoryEvent],
) -> bool:
    a_calls = [e for e in run_a if e.kind == "ToolCall" and e.tool_name]
    b_calls = [e for e in run_b if e.kind == "ToolCall" and e.tool_name]
    names_a = [e.tool_name for e in a_calls]
    names_b = [e.tool_name for e in b_calls]
    # Identical tool-name sequences cannot be an order swap.
    if names_a == names_b:
        return False
    n = len(a_calls)
    m = len(b_calls)
    for i in range(n):
        for j in range(i + 1, n):
            ai, aj = a_calls[i], a_calls[j]
            if ai.tool_name == aj.tool_name:
                continue
            if _are_parallel(ai, aj):
                continue
            for p in range(m):
                for q in range(p + 1, m):
                    bp, bq = b_calls[p], b_calls[q]
                    if _are_parallel(bp, bq):
                        continue
                    if ai.tool_name == bq.tool_name and aj.tool_name == bp.tool_name:
                        return True
    return False


def _are_parallel(x: TrajectoryEvent, y: TrajectoryEvent) -> bool:
    return (
        x.parallel_group is not None
        and y.parallel_group is not None
        and x.parallel_group == y.parallel_group
    )
