#!/usr/bin/env python3
"""Sensitivity controls for the Ollama burn-in harness.

Deliberately perturbed self-pairs. Do not tune taxonomy/prompts/costs to pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from vernier.canonical._jcs import serialize_jcs
from vernier.replay.harness_guard import tool_call_count
from vernier.replay.project import project_run

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "testdata" / "corpus" / "MANIFEST.json"
OUT_MD = ROOT / "burnin_control.md"
STAGING = ROOT / "testdata" / "corpus" / "_burnin_control"
PROMPT_FILE = STAGING / "c3_prompt.txt"

LLM_BASE_URL = "http://localhost:11434"
ENDPOINT = "http://localhost:11434/v1"
BASE_MODEL = "qwen3:4b-instruct"
ALT_MODEL = "llama3.2:3b"
PER_RUN_TIMEOUT_S = 900
RUNS_PER_CONTROL = 5


@dataclass
class RunResult:
    control_id: str
    run_id: str
    label: str
    status: str
    severity: str
    rule_ids: list[str]
    baseline_tool_count: int
    candidate_tool_count: int
    wall_clock_s: float
    transport_log: str
    harness_fault: str = ""


@dataclass
class ControlSpec:
    control_id: str
    title: str
    expected: str
    # Return True if observed results satisfy the control.
    judge: Callable[[list[RunResult]], bool]
    # Expected rule IDs to highlight in the report (may be empty for C1).
    expected_rules: tuple[str, ...] = ()
    prepare_env: Callable[[dict[str, str]], None] | None = None
    extra_diff_args: Callable[[], list[str]] | None = None
    prepare_run_dir: Callable[[Path, dict[str, Any]], None] | None = None


def _trim(text: str, *, limit: int = 12000) -> str:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return cleaned[-limit:] if len(cleaned) > limit else cleaned


def _parse_report(stdout: str) -> dict[str, Any] | None:
    text = (stdout or "").strip()
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return None


def _drop_tool_name_for_entry(entry: dict[str, Any]) -> str:
    """First tool exposed by the agent script (used for C4 schema drop)."""
    script = str(entry.get("script") or "examples/research_agent.py")
    # Stable mapping — matches TOOLS dict key order in each agent.
    first_tools = {
        "examples/research_agent.py": "web_search",
        "examples/data_agent.py": "sql_query",
        "examples/support_agent.py": "lookup_customer",
        "examples/coding_agent.py": "read_file",
        "examples/booking_agent.py": "search_flights",
        "examples/router_agent.py": "classify_intent",
    }
    return first_tools.get(script, "web_search")


def _record_control_baselines() -> Path:
    """Record 5 research runs against Ollama for sensitivity controls.

    The v0 corpus is invalid; controls need model-driven baselines first.
    """
    out = ROOT / "testdata" / "corpus"
    runs_dir = out / "runs"
    if runs_dir.exists():
        shutil.rmtree(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    inputs = ["climate", "antibiotics", "llms", "urban-heat", "oceans"]
    entries: list[dict[str, Any]] = []
    env = _base_env()
    env["VERNIER_LLM_TEMPERATURE"] = "0"
    tmp = out / "_tmp_control_record"
    for index, input_value in enumerate(inputs, start=1):
        label = f"research_{index:02d}"
        print(f"record control baseline {label} input={input_value!r} …", flush=True)
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        proc = subprocess.run(
            [
                "uv",
                "run",
                "vern",
                "record",
                "--out",
                str(tmp),
                "--",
                sys.executable,
                str(ROOT / "examples/research_agent.py"),
                "--input",
                input_value,
                "--model",
                BASE_MODEL,
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=PER_RUN_TIMEOUT_S,
        )
        dirs = [p for p in tmp.iterdir() if p.is_dir()]
        if not dirs:
            raise RuntimeError(
                f"control record failed for {label}:\n{proc.stdout}\n{proc.stderr}"
            )
        src = dirs[0]
        from vernier.record.store import load_run
        from vernier.replay.project import project_run as _project

        frozen = load_run(src)
        dest = runs_dir / frozen.run_id
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        manifest_path = dest / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["command"] = [
            "python",
            "examples/research_agent.py",
            "--input",
            input_value,
            "--model",
            BASE_MODEL,
        ]
        manifest["cwd"] = str(ROOT)
        manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")
        traj = _project(dest)
        tools = tool_call_count(traj)
        print(
            f"  OK {label} run_id={frozen.run_id} steps={len(traj)} tools={tools}",
            flush=True,
        )
        if tools < 1:
            raise RuntimeError(f"{label} recorded zero tools — agent not model-driven?")
        entries.append(
            {
                "run_id": frozen.run_id,
                "shape": "research",
                "label": label,
                "script": "examples/research_agent.py",
                "input": input_value,
                "baseline_model": BASE_MODEL,
                "event_count": frozen.manifest.event_count,
                "projected_steps": len(traj),
                "path": f"runs/{frozen.run_id}",
            }
        )
    if tmp.exists():
        shutil.rmtree(tmp)
    manifest_doc = {
        "version": "corpus-control-v1",
        "baseline_model": BASE_MODEL,
        "run_count": len(entries),
        "shapes": ["research"],
        "note": "Temporary control baselines (Ollama). Full corpus rebuilt after C1–C4 pass.",
        "runs": entries,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest_doc, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST} ({len(entries)} control baselines)", flush=True)
    return MANIFEST


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env["VERNIER_LLM_BASE_URL"] = LLM_BASE_URL
    env["OPENAI_BASE_URL"] = LLM_BASE_URL
    env.setdefault("OPENAI_API_KEY", "ollama")
    env.setdefault("VERNIER_LLM_API_KEY", "ollama")
    env["VERNIER_LLM_SEED"] = "42"
    env["VERNIER_LLM_NUM_CTX"] = "16384"
    env["VERNIER_LLM_STREAM"] = "false"
    env.setdefault("VERNIER_LLM_TIMEOUT_S", "600")
    env["VERNIER_LLM_TEMPERATURE"] = "0"
    env.pop("VERNIER_CONTROL_DROP_TOOL", None)
    return env


def _run_diff(
    *,
    control_id: str,
    entry: dict[str, Any],
    runs_dir: Path,
    diff_out: Path,
    env: dict[str, str],
    model: str,
    extra_args: list[str],
    prepare_run_dir: Callable[[Path, dict[str, Any]], None] | None,
) -> RunResult:
    run_id = str(entry["run_id"])
    label = str(entry.get("label") or "")
    src = ROOT / "testdata" / "corpus" / str(entry["path"])
    dest = runs_dir / run_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    manifest_path = dest / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cwd"] = str(ROOT)
    manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")
    if prepare_run_dir is not None:
        prepare_run_dir(dest, entry)

    cmd = [
        "uv",
        "run",
        "vern",
        "diff",
        run_id,
        "--model",
        model,
        "--runs-dir",
        str(runs_dir),
        "--out",
        str(diff_out),
        "--json",
        *extra_args,
    ]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=PER_RUN_TIMEOUT_S,
        )
        timed_out = False
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        rc = proc.returncode
        combined = stderr + "\n" + stdout
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (
            exc.stdout
            if isinstance(exc.stdout, str)
            else (exc.stdout.decode("utf-8", errors="replace") if exc.stdout else "")
        )
        stderr = (
            exc.stderr
            if isinstance(exc.stderr, str)
            else (exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "")
        )
        rc = -1
        combined = stderr + "\n" + stdout + f"\nTIMEOUT after {PER_RUN_TIMEOUT_S}s"

    wall = time.perf_counter() - t0
    base_tools = tool_call_count(project_run(dest)) if dest.is_dir() else 0
    report = None if timed_out else _parse_report(stdout)
    cand_tools = 0
    severity = ""
    rule_ids: list[str] = []
    status = "operational_error"
    harness_fault = ""

    if report and isinstance(report.get("candidate_run_id"), str):
        cand_dir = diff_out / str(report["candidate_run_id"])
        if cand_dir.is_dir():
            cand_tools = tool_call_count(project_run(cand_dir))

    if timed_out:
        status = "operational_error"
        severity = "OPS_TIMEOUT"
    elif report and isinstance(report.get("verdict"), dict):
        verdict = report["verdict"]
        hf = verdict.get("harness_fault")
        if isinstance(hf, str) and hf:
            status = "HARNESS_FAULT"
            harness_fault = hf
            severity = "HARNESS_FAULT"
        else:
            status = "completed"
            severity = str(verdict.get("severity") or "")
            raw_rules = verdict.get("rule_ids") or []
            if isinstance(raw_rules, list):
                rule_ids = [str(x) for x in raw_rules]
            elif raw_rules:
                rule_ids = [str(raw_rules)]
    elif rc == 2:
        status = "operational_error"
        severity = "OPS_ERROR"
    else:
        status = "operational_error"
        severity = "PARSE_ERROR"

    return RunResult(
        control_id=control_id,
        run_id=run_id,
        label=label,
        status=status,
        severity=severity,
        rule_ids=rule_ids,
        baseline_tool_count=base_tools,
        candidate_tool_count=cand_tools,
        wall_clock_s=wall,
        transport_log=_trim(combined),
        harness_fault=harness_fault,
    )


def _judge_c1(results: list[RunResult]) -> bool:
    return any(r.severity not in {"", "NONE"} for r in results)


def _judge_rules(
    results: list[RunResult], wanted: set[str]
) -> bool:
    observed: set[str] = set()
    for r in results:
        observed.update(r.rule_ids)
    return bool(observed & wanted)


def _controls() -> list[ControlSpec]:
    def prep_c1(env: dict[str, str]) -> None:
        env["VERNIER_LLM_TEMPERATURE"] = "1.0"

    def prep_c2(env: dict[str, str]) -> None:
        env["VERNIER_LLM_TEMPERATURE"] = "0"

    def args_c3() -> list[str]:
        PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROMPT_FILE.write_text(
            "Always call the first available tool twice.\n",
            encoding="utf-8",
            newline="\n",
        )
        return ["--prompt-file", str(PROMPT_FILE)]

    return [
        ControlSpec(
            control_id="C1",
            title="candidate temperature=1.0 (baseline 0)",
            expected="at least one non-NONE severity across 5 runs",
            expected_rules=(),
            judge=_judge_c1,
            prepare_env=prep_c1,
        ),
        ControlSpec(
            control_id="C2",
            title=f"candidate model={ALT_MODEL}",
            expected="D04 and/or D05 and/or D06 fires",
            expected_rules=("D04", "D05", "D06"),
            judge=lambda rs: _judge_rules(rs, {"D04", "D05", "D06"}),
            prepare_env=prep_c2,
            # model passed via vern --model in runner
        ),
        ControlSpec(
            control_id="C3",
            title='--prompt-file "Always call the first available tool twice."',
            expected="D06 or D08 fires",
            expected_rules=("D06", "D08"),
            judge=lambda rs: _judge_rules(rs, {"D06", "D08"}),
            prepare_env=prep_c2,
            extra_diff_args=args_c3,
        ),
        ControlSpec(
            control_id="C4",
            title="one tool removed from schema (VERNIER_CONTROL_DROP_TOOL)",
            expected="D05 fires",
            expected_rules=("D05",),
            judge=lambda rs: _judge_rules(rs, {"D05"}),
            prepare_env=_prep_c4,
        ),
    ]


def _prep_c4(env: dict[str, str]) -> None:
    env["VERNIER_LLM_TEMPERATURE"] = "0"
    # Dropped for research baselines recorded by this script.
    env["VERNIER_CONTROL_DROP_TOOL"] = "web_search"


def _write_report(
    path: Path,
    *,
    controls: list[ControlSpec],
    results_by_control: dict[str, list[RunResult]],
    verdicts: dict[str, bool],
) -> None:
    lines: list[str] = [
        "# Burn-in sensitivity controls",
        "",
        f"Endpoint: `{ENDPOINT}`",
        f"Baseline model: `{BASE_MODEL}`",
        f"Runs per control: {RUNS_PER_CONTROL}",
        "",
    ]
    for spec in controls:
        rows = results_by_control[spec.control_id]
        passed = verdicts[spec.control_id]
        observed_rules: list[str] = []
        seen: set[str] = set()
        for r in rows:
            for rid in r.rule_ids:
                if rid not in seen:
                    seen.add(rid)
                    observed_rules.append(rid)
        sevs = sorted({r.severity or "(empty)" for r in rows})
        lines.extend(
            [
                f"## {spec.control_id} — {'PASS' if passed else 'FAIL'}",
                "",
                f"- **setup:** {spec.title}",
                f"- **expected:** {spec.expected}",
                f"- **expected rule IDs:** "
                + (
                    ", ".join(spec.expected_rules)
                    if spec.expected_rules
                    else "(any non-NONE severity)"
                ),
                f"- **observed rule IDs:** "
                + (", ".join(observed_rules) if observed_rules else "(none)"),
                f"- **observed severities:** {', '.join(sevs)}",
                f"- **verdict:** {'PASS' if passed else 'FAIL'}",
                "",
                "| run | label | status | severity | rule_ids | tools | wall_s |",
                "| --- | --- | --- | --- | --- | --- | ---: |",
            ]
        )
        for r in rows:
            rules = "|".join(r.rule_ids) if r.rule_ids else ""
            lines.append(
                f"| `{r.run_id[:8]}…` | {r.label} | {r.status} | {r.severity} | "
                f"{rules or '—'} | {r.baseline_tool_count}→{r.candidate_tool_count} | "
                f"{r.wall_clock_s:.1f} |"
            )
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=OUT_MD)
    parser.add_argument(
        "--controls",
        default="C1,C2,C3,C4",
        help="Comma-separated control ids to run (default all).",
    )
    parser.add_argument(
        "--record-baselines",
        action="store_true",
        help="Force re-record of Ollama control baselines before running.",
    )
    args = parser.parse_args(argv)

    need_record = args.record_baselines
    if not MANIFEST.is_file():
        need_record = True
    else:
        data_probe = json.loads(MANIFEST.read_text(encoding="utf-8"))
        runs_probe = data_probe.get("runs") or []
        if len(runs_probe) < RUNS_PER_CONTROL:
            need_record = True
        elif data_probe.get("baseline_model") != BASE_MODEL:
            # Old stub corpus / placeholder — not valid for model-driven controls.
            need_record = True
        else:
            # Ensure at least one run path exists on disk.
            first = runs_probe[0]
            if not (ROOT / "testdata" / "corpus" / str(first["path"])).is_dir():
                need_record = True

    if need_record:
        print("recording Ollama control baselines (model-driven agents)…", flush=True)
        _record_control_baselines()

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = list(data["runs"])[:RUNS_PER_CONTROL]
    if len(entries) < RUNS_PER_CONTROL:
        print(f"need ≥{RUNS_PER_CONTROL} corpus runs", file=sys.stderr)
        return 2

    wanted = {c.strip() for c in args.controls.split(",") if c.strip()}
    controls = [c for c in _controls() if c.control_id in wanted]
    if not controls:
        print(f"no controls selected from {wanted}", file=sys.stderr)
        return 2

    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)

    results_by_control: dict[str, list[RunResult]] = {}
    verdicts: dict[str, bool] = {}
    failing_logs: list[tuple[ControlSpec, RunResult]] = []

    print(
        f"burnin_control: endpoint={ENDPOINT} base_model={BASE_MODEL} "
        f"controls={[c.control_id for c in controls]}",
        flush=True,
    )

    for spec in controls:
        print(f"\n=== {spec.control_id}: {spec.title} ===", flush=True)
        runs_dir = STAGING / spec.control_id.lower() / "runs"
        diff_out = STAGING / spec.control_id.lower() / "diff-runs"
        runs_dir.mkdir(parents=True)
        diff_out.mkdir(parents=True)

        env = _base_env()
        if spec.prepare_env:
            spec.prepare_env(env)

        model = ALT_MODEL if spec.control_id == "C2" else BASE_MODEL
        extra = spec.extra_diff_args() if spec.extra_diff_args else []

        rows: list[RunResult] = []
        for i, entry in enumerate(entries, start=1):
            print(
                f"{spec.control_id} {i}/{RUNS_PER_CONTROL}: "
                f"{entry.get('label')} {entry['run_id']} …",
                flush=True,
            )
            row = _run_diff(
                control_id=spec.control_id,
                entry=entry,
                runs_dir=runs_dir,
                diff_out=diff_out,
                env=env,
                model=model,
                extra_args=extra,
                prepare_run_dir=spec.prepare_run_dir,
            )
            rows.append(row)
            print(
                f"  status={row.status} severity={row.severity} "
                f"rules={row.rule_ids or '-'} "
                f"tools={row.baseline_tool_count}→{row.candidate_tool_count} "
                f"wall={row.wall_clock_s:.1f}s",
                flush=True,
            )

        passed = spec.judge(rows)
        results_by_control[spec.control_id] = rows
        verdicts[spec.control_id] = passed
        print(
            f"{spec.control_id} => {'PASS' if passed else 'FAIL'}",
            flush=True,
        )
        if not passed:
            # Prefer a completed NONE run for the transport dump; else first row.
            pick = next(
                (
                    r
                    for r in rows
                    if r.severity == "NONE" and r.status == "completed"
                ),
                rows[0],
            )
            failing_logs.append((spec, pick))

    _write_report(
        args.summary,
        controls=controls,
        results_by_control=results_by_control,
        verdicts=verdicts,
    )
    print(f"\nwrote {args.summary}", flush=True)

    for spec, row in failing_logs:
        print(
            f"\n--- transport log ({spec.control_id} fail example "
            f"run_id={row.run_id}) ---",
            flush=True,
        )
        print(row.transport_log, flush=True)

    if not all(verdicts[c.control_id] for c in controls):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
