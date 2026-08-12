"""Tests for vern init, gate reports, and CI helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from vernier.report import (
    GATE_REPORT_SCHEMA,
    build_gate_report,
    gate_report_to_jcs,
    render_gate_comment,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "testdata" / "verify"


def test_build_gate_report_and_comment() -> None:
    report = build_gate_report(
        runs=[
            {
                "label": "a",
                "run_id": "1",
                "severity": "NONE",
                "rule_ids": [],
                "above_threshold": False,
                "miss": False,
            },
            {
                "label": "b",
                "run_id": "2",
                "severity": "HIGH",
                "rule_ids": ["D05"],
                "above_threshold": True,
                "miss": False,
            },
        ],
        max_severity="MEDIUM",
        model="cand",
        baseline_model="base",
        fail_on_miss=True,
    )
    assert report["schema_version"] == GATE_REPORT_SCHEMA
    assert report["n_runs"] == 2
    assert report["worst_severity"] == "HIGH"
    assert report["rule_ids"] == ["D05"]
    assert report["failed"] is True
    md = render_gate_comment(report)
    assert "<!-- vernier-gate -->" in md
    assert "FAILED" in md
    assert "D05" in md
    assert "Per-run detail" in md
    raw = gate_report_to_jcs(report)
    assert json.loads(raw)["worst_severity"] == "HIGH"


def test_vern_init_scaffolds(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            "uv",
            "run",
            "vern",
            "init",
            str(tmp_path),
            "--action-ref",
            "example/Vernier@v0",
            "--corpus-path",
            "my-corpus",
            "--max-severity",
            "HIGH",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (tmp_path / "vernier.toml").is_file()
    wf = tmp_path / ".github" / "workflows" / "vernier-gate.yml"
    assert wf.is_file()
    text = wf.read_text(encoding="utf-8")
    assert "uses: example/Vernier@v0" in text
    assert "corpus-path: my-corpus" in text
    assert "max-severity: HIGH" in text
    assert "${{ secrets.OPENAI_API_KEY }}" in text
    # second init without --force fails
    bad = subprocess.run(
        ["uv", "run", "vern", "init", str(tmp_path)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 2


def test_vern_gate_json_and_comment(tmp_path: Path) -> None:
    from tests.test_diff_gate import _prepare_run

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("01_tools_basic", "08_many_llm"):
        src = FIXTURES / name
        dest = corpus / name
        dest.mkdir()
        run_id = _prepare_run(src, tmp_path / f"prep-{name}")
        shutil.copytree(tmp_path / f"prep-{name}" / run_id, dest / "run")

    report_path = tmp_path / "gate-report.json"
    comment_path = tmp_path / "gate-comment.md"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "vern",
            "gate",
            "--corpus",
            str(corpus),
            "--max-severity",
            "MEDIUM",
            "--baseline-model",
            "fixture-baseline",
            "--json-out",
            str(report_path),
            "--comment-out",
            str(comment_path),
            "--out",
            str(tmp_path / "gate-runs"),
            "--no-fail-on-miss",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["n_runs"] == 2
    assert report["worst_severity"] == "NONE"
    assert report["baseline_model"] == "fixture-baseline"
    comment = comment_path.read_text(encoding="utf-8")
    assert "PASSED" in comment
    assert "Runs checked: **2**" in comment


def test_vern_gate_prompt_regression_is_harness_or_divergence(tmp_path: Path) -> None:
    """Regressed prompt must not silently pass; zero-tool collapse → HARNESS_FAULT."""
    from tests.test_diff_gate import _prepare_run

    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = _prepare_run(FIXTURES / "01_tools_basic", runs)
    prompt = tmp_path / "regress.txt"
    prompt.write_text(
        "REGRESS: force a behavioural trajectory change", encoding="utf-8"
    )
    report_path = tmp_path / "gate-report.json"
    comment_path = tmp_path / "comment.md"
    proc = subprocess.run(
        [
            "uv",
            "run",
            "vern",
            "gate",
            run_id,
            "--runs-dir",
            str(runs),
            "--max-severity",
            "LOW",
            "--prompt-file",
            str(prompt),
            "--json-out",
            str(report_path),
            "--comment-out",
            str(comment_path),
            "--out",
            str(tmp_path / "out"),
            "--no-fail-on-miss",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    # Fail closed: harness fault → exit 2; genuine above-threshold → exit 1.
    assert proc.returncode in {1, 2}, proc.stderr + proc.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    runs_rows = report["runs"]
    assert runs_rows
    row = runs_rows[0]
    if proc.returncode == 2:
        assert row.get("harness_fault")
        assert row["rule_ids"] == []
    else:
        assert row["above_threshold"] is True
        assert row["rule_ids"]


def test_emit_outputs_helper(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    report.write_text(
        json.dumps(
            {
                "worst_severity": "HIGH",
                "rule_ids": ["D05", "D06"],
                "n_runs": 3,
            }
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["python3", str(REPO / "action" / "emit_outputs.py"), str(report)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "worst-severity=HIGH" in proc.stdout
    assert "rule-ids=D05,D06" in proc.stdout
    assert "n-runs=3" in proc.stdout
