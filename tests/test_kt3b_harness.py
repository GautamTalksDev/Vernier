"""KT-3b: unchanged-variable live-diff must return severity NONE.

If live-diff cannot reproduce the baseline when nothing changed, the harness
is broken by definition (see README kill tests).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from vernier.canonical._jcs import serialize_jcs
from vernier.classify import classify_pair
from vernier.classify.types import (
    HARNESS_FAULT_ZERO_TOOL_COLLAPSE,
    TrajectoryEvent,
    Verdict,
    Severity,
)
from vernier.replay.harness_guard import apply_harness_guard
from vernier.replay.redrive import RedriveResult, redrive

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "testdata" / "verify"
AGENT = REPO / "examples" / "verify_fixture_agent.py"
CORPUS_MANIFEST = REPO / "testdata" / "corpus" / "MANIFEST.json"


def _prepare_verify_run(case: Path, dest_runs: Path) -> Path:
    from vernier.record.store import load_run

    frozen = load_run(case / "run")
    run_id = frozen.run_id
    target = dest_runs / run_id
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(case / "run", target)
    meta = json.loads((case / "meta.json").read_text(encoding="utf-8"))
    scenario = meta["scenario"]
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["command"] = [
        "python",
        str(AGENT.relative_to(REPO)),
        "--scenario",
        scenario,
    ]
    manifest["cwd"] = str(REPO)
    manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")
    return target


def test_kt3b_unchanged_live_diff_verify_fixture_is_none(tmp_path: Path) -> None:
    """Live-diff with no model/prompt override → severity NONE."""
    source = _prepare_verify_run(FIXTURES / "01_tools_basic", tmp_path / "runs")
    result = redrive(
        source,
        out_dir=tmp_path / "diff-runs",
        mode="live_diff",
        model=None,
        prompt_file=None,
    )
    assert result.verdict.harness_fault is None, result.verdict.harness_fault
    assert result.verdict.severity.value == "NONE"
    assert list(result.verdict.rule_ids) == []
    cand_tools = sum(1 for e in result.traj_b if e.kind == "ToolCall")
    base_tools = sum(1 for e in result.traj_a if e.kind == "ToolCall")
    assert cand_tools == base_tools
    assert cand_tools > 0


@pytest.mark.skipif(
    not CORPUS_MANIFEST.is_file(),
    reason="corpus manifest missing",
)
def test_kt3b_live_diff_corpus_dials_real_llm(tmp_path: Path) -> None:
    """Corpus agent under live-diff must reach Ollama and execute tools.

    Null-pair severity is not asserted: model-level D04 on research is a
    known floor (see null_floor.md). This test only guards harness health.
    """
    data = json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))
    entry = next(e for e in data["runs"] if e["shape"] == "data")
    src = REPO / "testdata" / "corpus" / entry["path"]
    if not src.is_dir():
        pytest.skip("corpus raw runs not present (gitignored); rebuild locally")
    dest = tmp_path / "runs" / entry["run_id"]
    shutil.copytree(src, dest)
    manifest_path = dest / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cwd"] = str(REPO)
    manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")

    result = redrive(
        dest,
        out_dir=tmp_path / "diff-runs",
        mode="live_diff",
        model=None,
    )
    assert "replay.invalid" not in (result.agent_output or "")
    assert result.verdict.harness_fault is None, result.verdict.harness_fault
    assert sum(1 for e in result.traj_b if e.kind == "ToolCall") > 0


def _fake_result(
    *,
    traj_a: list[TrajectoryEvent],
    traj_b: list[TrajectoryEvent],
    classified: Verdict,
    agent_exit: int,
    agent_output: str,
) -> RedriveResult:
    return RedriveResult(
        source_run_id="a",
        candidate_run_id="b",
        source_dir=Path("."),
        candidate_dir=Path("."),
        traj_a=traj_a,
        traj_b=traj_b,
        pairs=(),
        verdict=classified,
        agent_exit=agent_exit,
        agent_output=agent_output,
    )


def test_harness_fault_on_crash_zero_tools_suppresses_d05() -> None:
    baseline = [
        TrajectoryEvent(kind="LlmTurn"),
        TrajectoryEvent(kind="ToolCall", tool_name="web_search", arguments={"q": "x"}),
        TrajectoryEvent(kind="ToolResult"),
        TrajectoryEvent(kind="Terminal", terminal_kind="FinalMessage", final_message="ok"),
    ]
    collapsed = [
        TrajectoryEvent(kind="LlmTurn"),
        TrajectoryEvent(kind="Terminal", terminal_kind="FinalMessage", final_message="err"),
    ]
    # Without guard, D05 would fire.
    classified = classify_pair(baseline, collapsed)
    assert "D05" in classified.rule_ids
    result = _fake_result(
        traj_a=baseline,
        traj_b=collapsed,
        classified=classified,
        agent_exit=1,
        agent_output="httpx.ConnectError: [Errno -2] Name or service not known",
    )
    guarded = apply_harness_guard(result)
    assert guarded.harness_fault == HARNESS_FAULT_ZERO_TOOL_COLLAPSE
    assert guarded.severity is Severity.NONE
    assert guarded.rule_ids == ()


def test_intentional_zero_tool_skip_keeps_d05() -> None:
    baseline = [
        TrajectoryEvent(kind="ToolCall", tool_name="only", arguments={}),
        TrajectoryEvent(kind="ToolResult"),
        TrajectoryEvent(kind="Terminal", terminal_kind="FinalMessage"),
    ]
    skipped = [
        TrajectoryEvent(kind="Terminal", terminal_kind="FinalMessage"),
    ]
    classified = classify_pair(baseline, skipped)
    assert "D05" in classified.rule_ids
    result = _fake_result(
        traj_a=baseline,
        traj_b=skipped,
        classified=classified,
        agent_exit=0,
        agent_output="",
    )
    guarded = apply_harness_guard(result)
    assert guarded.harness_fault is None
    assert "D05" in guarded.rule_ids
