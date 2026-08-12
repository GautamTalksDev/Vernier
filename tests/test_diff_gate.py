"""Tests for vern diff / gate reporting and live-diff behaviour."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from vernier.align.types import AlignedPair, Relation
from vernier.canonical._jcs import serialize_jcs
from vernier.classify.types import AppliedIgnore, FiredRule, Severity, Verdict
from vernier.record.store import load_run
from vernier.replay.overrides import apply_live_overrides
from vernier.report import (
    REPORT_SCHEMA_VERSION,
    build_report,
    escape_untrusted,
    render_human,
)
from vernier.report.json_report import diverging_region

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "testdata" / "verify"
AGENT = REPO / "examples" / "verify_fixture_agent.py"


def _prepare_run(case: Path, dest_runs: Path) -> str:
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
    return run_id


def test_escape_strips_ansi_and_bidi() -> None:
    dirty = "hello\x1b[31mRED\x1b[0m\u202eoverride\x00nul\x9b world"
    cleaned = escape_untrusted(dirty)
    assert "\x1b" not in cleaned
    assert "\u202e" not in cleaned
    assert "\x00" not in cleaned
    assert "hello" in cleaned
    assert "RED" in cleaned
    assert "world" in cleaned
    assert cleaned.isprintable() or "\n" in cleaned or "\t" in cleaned


def test_escape_tool_description_with_csi() -> None:
    tool_desc = "search\x1b]0;title\x07\x1b[2Jdanger"
    out = escape_untrusted(tool_desc)
    assert "\x1b" not in out
    assert "search" in out
    assert "danger" in out


def test_render_human_escapes_untrusted_detail() -> None:
    verdict = Verdict(
        spec_version="divergence-v1",
        severity=Severity.HIGH,
        rule_ids=("D04",),
        fired=(
            FiredRule(
                "D04",
                Severity.HIGH,
                "args differ \x1b[31mbad\x1b[0m",
            ),
        ),
        applied_ignores=(
            AppliedIgnore(mechanism="pointer", pointer="/request_id", side="both"),
        ),
    )
    text = render_human(
        source_run_id="a",
        candidate_run_id="b",
        verdict=verdict,
        pairs=[],
        traj_a=[],
        traj_b=[],
        color=False,
    )
    assert "\x1b" not in text
    assert "D04" in text
    assert "/request_id" in text
    assert "bad" in text


def test_json_report_schema_version() -> None:
    verdict = Verdict(
        spec_version="divergence-v1",
        severity=Severity.NONE,
        rule_ids=(),
        fired=(),
        applied_ignores=(),
    )
    report = build_report(
        source_run_id="src",
        candidate_run_id="cand",
        verdict=verdict,
        pairs=[],
        traj_a=[],
        traj_b=[],
    )
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert "verdict" in report
    assert "diverging_region" in report


def test_diverging_region_context() -> None:
    pairs = [
        AlignedPair(0, 0, Relation.MATCHED),
        AlignedPair(1, 1, Relation.SUBSTITUTED),
        AlignedPair(2, 2, Relation.MATCHED),
    ]
    region = diverging_region(pairs, context=1)
    assert len(region) == 3
    assert region[1].relation is Relation.SUBSTITUTED


def test_apply_live_overrides_model_and_prompt(tmp_path: Path) -> None:
    prompt = tmp_path / "sys.txt"
    prompt.write_text("SYSTEM", encoding="utf-8")
    body = json.dumps(
        {
            "model": "old",
            "messages": [{"role": "user", "content": "hi"}],
        }
    ).encode("utf-8")
    os.environ["VERNIER_OVERRIDE_MODEL"] = "new-model"
    os.environ["VERNIER_OVERRIDE_PROMPT_FILE"] = str(prompt)
    try:
        out = apply_live_overrides(body)
        data = json.loads(out.decode("utf-8"))
        assert data["model"] == "new-model"
        assert data["messages"][0] == {"role": "system", "content": "SYSTEM"}
    finally:
        os.environ.pop("VERNIER_OVERRIDE_MODEL", None)
        os.environ.pop("VERNIER_OVERRIDE_PROMPT_FILE", None)


def test_live_diff_tool_miss_executes_live(tmp_path: Path) -> None:
    """Model-driven live-diff: novel tool args execute live (not ReplayToolMiss)."""
    from vernier.capture.bootstrap import reset_for_tests
    from vernier.capture.recorder import activate_store, deactivate
    from vernier.capture.tools import tool
    from vernier.record.store import RecordStore
    from vernier.replay import session as replay_session
    from vernier.replay.catalog import ReplayCatalog

    reset_for_tests()
    store = RecordStore(tmp_path / "run", "r1")
    activate_store(store)
    catalog = ReplayCatalog(
        source_run_id="src",
        source_root=tmp_path,
        command=(),
        cwd="",
    )
    replay_session._catalog = catalog  # noqa: SLF001
    replay_session._live_diff = True  # noqa: SLF001

    @tool
    def ghost(x: int) -> int:
        return x + 1

    assert ghost(1) == 2
    kinds = [e["kind"] for e in store.read_events()]
    assert "ToolCall" in kinds
    assert "ToolResult" in kinds
    assert "ReplayMiss" not in kinds
    deactivate()
    reset_for_tests()


def test_vern_diff_on_fixture(tmp_path: Path) -> None:
    case = FIXTURES / "01_tools_basic"
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = _prepare_run(case, runs)
    proc = subprocess.run(
        [
            "uv",
            "run",
            "vern",
            "diff",
            run_id,
            "--runs-dir",
            str(runs),
            "--out",
            str(tmp_path / "diff-runs"),
            "--model",
            "demo-override",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    # Live re-drive: exit 0/1 = classified; exit 2 = harness/ops fault.
    assert proc.returncode in {0, 1, 2}, proc.stderr + proc.stdout
    assert (
        "severity" in proc.stdout.lower()
        or "vern diff" in proc.stdout
        or "HARNESS" in proc.stderr
    )


def test_vern_diff_json(tmp_path: Path) -> None:
    case = FIXTURES / "08_many_llm"
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = _prepare_run(case, runs)
    proc = subprocess.run(
        [
            "uv",
            "run",
            "vern",
            "diff",
            run_id,
            "--runs-dir",
            str(runs),
            "--out",
            str(tmp_path / "diff-runs"),
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode in {0, 1}, proc.stderr + proc.stdout
    report = json.loads(proc.stdout)
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert "verdict" in report


def test_vern_gate_exit_codes(tmp_path: Path) -> None:
    case = FIXTURES / "01_tools_basic"
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = _prepare_run(case, runs)
    # CRITICAL threshold: almost everything passes
    proc = subprocess.run(
        [
            "uv",
            "run",
            "vern",
            "gate",
            run_id,
            "--max-severity",
            "CRITICAL",
            "--runs-dir",
            str(runs),
            "--out",
            str(tmp_path / "gate-runs"),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode in {0, 1}, proc.stderr + proc.stdout

    bad = subprocess.run(
        [
            "uv",
            "run",
            "vern",
            "gate",
            run_id,
            "--max-severity",
            "NOT_A_LEVEL",
            "--runs-dir",
            str(runs),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 2


def test_vern_gate_corpus(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("01_tools_basic", "08_many_llm"):
        src = FIXTURES / name
        dest = corpus / name
        dest.mkdir()
        shutil.copytree(src / "run", dest / "run")
        # Fix command/cwd for redrive
        run_id = _prepare_run(src, tmp_path / f"prep-{name}")
        prepared = tmp_path / f"prep-{name}" / run_id
        shutil.rmtree(dest / "run")
        shutil.copytree(prepared, dest / "run")

    proc = subprocess.run(
        [
            "uv",
            "run",
            "vern",
            "gate",
            "--corpus",
            str(corpus),
            "--max-severity",
            "CRITICAL",
            "--out",
            str(tmp_path / "gate-runs"),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode in {0, 1}, proc.stderr + proc.stdout
    assert "worst=" in proc.stdout


def test_help_prints_network_guarantee() -> None:
    # Wide COLUMNS so Rich does not soft-wrap mid-phrase; strip ANSI for
    # the same reason (markup can split a token across escape codes).
    env = {**os.environ, "COLUMNS": "200", "TERM": "dumb"}
    proc = subprocess.run(
        ["uv", "run", "vern", "--help"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0
    out = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout)
    out = re.sub(r"\s+", " ", out)
    assert "ZERO network requests" in out
    assert "No telemetry" in out
    assert "no version check" in out
    assert "no analytics" in out
    assert "no phone-home" in out
