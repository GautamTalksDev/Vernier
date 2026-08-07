"""Trajectory aligner: fixtures, tie-break, length guard, golden bytes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vernier.align import (
    AlignedPair,
    AlignEvent,
    AlignKind,
    AlignmentTooLongError,
    Relation,
    align,
    load_costs,
    serialize_alignment,
)
from vernier.align.costs import AlignCosts

FIXTURES = Path(__file__).resolve().parents[1] / "testdata" / "align"


def _load_events(path: Path) -> list[AlignEvent]:
    data = json.loads(path.read_text(encoding="utf-8"))
    events: list[AlignEvent] = []
    for item in data:
        kind = AlignKind(item["kind"])
        tool = item.get("tool_name")
        events.append(AlignEvent(kind, tool_name=tool))
    return events


def _fixture_dirs() -> list[Path]:
    dirs = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
    assert len(dirs) == 25, f"expected 25 fixtures, found {len(dirs)}"
    return dirs


@pytest.mark.parametrize("case_dir", _fixture_dirs(), ids=lambda p: p.name)
def test_fixture_alignment_matches_golden_bytes(case_dir: Path) -> None:
    a = _load_events(case_dir / "a.json")
    b = _load_events(case_dir / "b.json")
    pairs = align(a, b)
    actual = serialize_alignment(pairs) + b"\n"
    expected = (case_dir / "expected.jcs").read_bytes()
    assert actual == expected


def test_identical_all_matched_zero_indels() -> None:
    case = FIXTURES / "01_identical"
    pairs = align(_load_events(case / "a.json"), _load_events(case / "b.json"))
    assert pairs
    assert all(p.relation is Relation.MATCHED for p in pairs)
    assert not any(p.relation in (Relation.INSERTED, Relation.DELETED) for p in pairs)


def test_reordered_parallel_aligns_cleanly() -> None:
    case = FIXTURES / "05_reordered_parallel"
    a = _load_events(case / "a.json")
    b = _load_events(case / "b.json")
    pairs = align(a, b)
    # Reorder must be Del/Ins + Match of the shared tool name — never Substituted.
    assert not any(p.relation is Relation.SUBSTITUTED for p in pairs)
    assert any(p.relation is Relation.INSERTED for p in pairs)
    assert any(p.relation is Relation.DELETED for p in pairs)
    matched_tool_calls = [
        a[p.a_idx].tool_name
        for p in pairs
        if p.relation is Relation.MATCHED
        and p.a_idx is not None
        and a[p.a_idx].kind is AlignKind.TOOL_CALL
    ]
    assert "alpha" in matched_tool_calls or "beta" in matched_tool_calls


def test_unrelated_mostly_unaligned() -> None:
    case = FIXTURES / "07_unrelated"
    pairs = align(_load_events(case / "a.json"), _load_events(case / "b.json"))
    indels = sum(
        1 for p in pairs if p.relation in (Relation.INSERTED, Relation.DELETED)
    )
    subs = sum(1 for p in pairs if p.relation is Relation.SUBSTITUTED)
    matched = sum(1 for p in pairs if p.relation is Relation.MATCHED)
    assert matched == 0
    assert indels > subs


def test_deterministic_tie_break_prefers_lower_source_index() -> None:
    """Equal scores → prefer consuming lower source (A) index first.

    When diagonal / up / left scores tie, DIAG wins over UP over LEFT.
    """
    costs = AlignCosts(
        version="test",
        max_events=5000,
        gap=0,
        match={
            "LlmTurn": 0,
            "ToolResult": 0,
            "Terminal": 0,
            "ToolCall_same_name": 0,
            "ToolCall_diff_name": -50,
        },
        mismatch_default=-50,
        mismatch_pairs={},
    )
    a = [AlignEvent(AlignKind.LLM_TURN), AlignEvent(AlignKind.LLM_TURN)]
    b = [AlignEvent(AlignKind.LLM_TURN), AlignEvent(AlignKind.LLM_TURN)]
    pairs = align(a, b, costs=costs)
    assert [(p.a_idx, p.b_idx, p.relation) for p in pairs] == [
        (0, 0, Relation.MATCHED),
        (1, 1, Relation.MATCHED),
    ]

    costs2 = AlignCosts(
        version="test",
        max_events=5000,
        gap=-1,
        match={
            "LlmTurn": -2,
            "ToolResult": -2,
            "Terminal": -2,
            "ToolCall_same_name": -2,
            "ToolCall_diff_name": -2,
        },
        mismatch_default=-2,
        mismatch_pairs={},
    )
    a2 = [AlignEvent(AlignKind.TOOL_CALL, "only_in_a")]
    b2 = [AlignEvent(AlignKind.TOOL_CALL, "only_in_b")]
    # At (1,1): diag=up=left=-2 → DIAG wins.
    assert align(a2, b2, costs=costs2) == (AlignedPair(0, 0, Relation.SUBSTITUTED),)


def test_refuse_over_max_events() -> None:
    costs = load_costs()
    assert costs.max_events == 5000
    big = [AlignEvent(AlignKind.LLM_TURN)] * (costs.max_events + 1)
    with pytest.raises(AlignmentTooLongError, match="5000"):
        align(big, [AlignEvent(AlignKind.LLM_TURN)])
    with pytest.raises(AlignmentTooLongError, match="5000"):
        align([AlignEvent(AlignKind.LLM_TURN)], big)


def test_repeated_runs_byte_identical() -> None:
    case = FIXTURES / "05_reordered_parallel"
    a = _load_events(case / "a.json")
    b = _load_events(case / "b.json")
    first = serialize_alignment(align(a, b))
    for _ in range(20):
        assert serialize_alignment(align(a, b)) == first


def test_costs_file_is_versioned_data() -> None:
    path = Path(__file__).resolve().parents[1] / "src/vernier/align/costs-v1.toml"
    text = path.read_text(encoding="utf-8")
    assert 'version = "costs-v1"' in text
    assert "rationale" in text.lower()
    costs = load_costs(path)
    assert costs.version == "costs-v1"
