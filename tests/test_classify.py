"""Divergence-v1 classifier: fixtures, near-misses, ignore reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vernier.canonical._jcs import serialize_jcs
from vernier.classify import (
    ClassificationTooLongError,
    TrajectoryEvent,
    classify_pair,
    load_ignore_config,
    verdict_to_dict,
)
from vernier.classify.distance import word_distance_ratio
from vernier.classify.ignore import apply_ignores, merge_applied_ignores

FIXTURES = Path(__file__).resolve().parents[1] / "testdata" / "divergence"


def _load_run(path: Path) -> list[TrajectoryEvent]:
    data = json.loads(path.read_text(encoding="utf-8"))
    events: list[TrajectoryEvent] = []
    for item in data:
        events.append(
            TrajectoryEvent(
                kind=item["kind"],
                tool_name=item.get("tool_name"),
                arguments=item.get("arguments"),
                terminal_kind=item.get("terminal_kind"),
                final_message=item.get("final_message"),
                is_error=bool(item.get("is_error", False)),
                destructive=bool(item.get("destructive", False)),
                parallel_group=item.get("parallel_group"),
                retry_index=int(item.get("retry_index", 0)),
            )
        )
    return events


def _fixture_dirs() -> list[Path]:
    dirs = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
    assert len(dirs) >= 60, f"expected >=60 fixtures, found {len(dirs)}"
    return dirs


@pytest.mark.parametrize("case_dir", _fixture_dirs(), ids=lambda p: p.name)
def test_fixture_severity_and_rule_ids(case_dir: Path) -> None:
    a = _load_run(case_dir / "a.json")
    b = _load_run(case_dir / "b.json")
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    verdict = classify_pair(a, b)
    assert verdict.severity.value == expected["severity"]
    assert list(verdict.rule_ids) == expected["rule_ids"]
    # Golden JCS identity for expected.json contents
    actual_expected = (
        serialize_jcs(
            {"severity": verdict.severity.value, "rule_ids": list(verdict.rule_ids)}
        )
        + b"\n"
    )
    assert actual_expected == (case_dir / "expected.json").read_bytes()


@pytest.mark.parametrize(
    "case_name",
    [
        "D04_NM01_timestamp_only",
        "D04_NM02_uuid_only",
        "D04_NM03_request_id_pointer",
        "D04_NM04_trace_and_nonce",
        "D04_NM05_nested_header_request_id",
        "D04_NM06_tool_call_id",
    ],
)
def test_d04_near_miss_no_false_positive(case_name: str) -> None:
    case = FIXTURES / case_name
    verdict = classify_pair(_load_run(case / "a.json"), _load_run(case / "b.json"))
    assert "D04" not in verdict.rule_ids
    assert verdict.applied_ignores
    assert verdict.severity.value == "NONE"


@pytest.mark.parametrize(
    "case_name",
    [
        "D07_NM01_parallel_reorder",
        "D07_NM02_parallel_three",
        "D07_NM03_parallel_with_llm",
    ],
)
def test_d07_parallel_near_miss_no_false_positive(case_name: str) -> None:
    case = FIXTURES / case_name
    verdict = classify_pair(_load_run(case / "a.json"), _load_run(case / "b.json"))
    assert "D07" not in verdict.rule_ids


def test_each_rule_has_at_least_five_fixtures() -> None:
    counts: dict[str, int] = {f"D{i:02d}": 0 for i in range(1, 13)}
    for path in _fixture_dirs():
        rule = path.name.split("_")[0]
        if rule in counts:
            counts[rule] += 1
    for rule, count in counts.items():
        assert count >= 5, f"{rule} has only {count} fixtures"


def test_applied_ignores_use_both_side() -> None:
    a = [
        TrajectoryEvent(
            kind="ToolCall",
            tool_name="search",
            arguments={"q": "x", "request_id": "r1"},
        ),
        TrajectoryEvent(
            kind="Terminal", terminal_kind="FinalMessage", final_message="ok"
        ),
    ]
    b = [
        TrajectoryEvent(
            kind="ToolCall",
            tool_name="search",
            arguments={"q": "x", "request_id": "r2"},
        ),
        TrajectoryEvent(
            kind="Terminal", terminal_kind="FinalMessage", final_message="ok"
        ),
    ]
    verdict = classify_pair(a, b)
    assert any(
        i.mechanism == "pointer" and i.pointer == "/request_id" and i.side == "both"
        for i in verdict.applied_ignores
    )
    report = verdict_to_dict(verdict)
    assert "applied_ignores" in report
    assert report["rule_ids"] == []


def test_word_distance_threshold_boundary() -> None:
    # 3/10 = 0.3 → D12; 4/10 = 0.4 → D11
    assert word_distance_ratio(
        "a b c d e f g h i j",
        "a b c d e f g x y z",
    ) == pytest.approx(0.3)
    assert word_distance_ratio(
        "a b c d e f g h i j",
        "a b c d e f w x y z",
    ) == pytest.approx(0.4)


def test_refuse_too_long() -> None:
    huge = [TrajectoryEvent(kind="LlmTurn") for _ in range(5001)]
    with pytest.raises(ClassificationTooLongError):
        classify_pair(huge, [TrajectoryEvent(kind="LlmTurn")])


def test_merge_applied_ignores() -> None:
    cfg = load_ignore_config()
    _, a_ign = apply_ignores({"request_id": "1", "nonce": "n"}, cfg, side="a")
    _, b_ign = apply_ignores({"request_id": "2"}, cfg, side="b")
    merged = merge_applied_ignores(a_ign, b_ign)
    by_ptr = {i.pointer: i.side for i in merged}
    assert by_ptr["/request_id"] == "both"
    assert by_ptr["/nonce"] == "a"


def test_no_model_imports_on_align_and_classify_path() -> None:
    """No-model-in-the-loop: align + classify stay free of model/ML stacks."""
    root = Path(__file__).resolve().parents[1] / "src" / "vernier"
    banned = (
        "openai",
        "anthropic",
        "embedding",
        "similarity",
        "numpy",
        "sklearn",
        "torch",
        "transformers",
        "sentence_transformers",
    )
    for pkg in ("align", "classify"):
        for path in (root / pkg).rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for token in banned:
                assert token not in text, f"{path} mentions banned token {token!r}"
