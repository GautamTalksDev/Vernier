#!/usr/bin/env python3
"""Harness burn-in against local Ollama (self-pair noise floor).

Diagnosis only — do not interpret results into RESULTS.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from vernier.canonical._jcs import serialize_jcs
from vernier.replay.harness_guard import tool_call_count
from vernier.replay.project import project_run

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "testdata" / "corpus" / "MANIFEST.json"
DEFAULT_CSV = ROOT / "burnin.csv"
DEFAULT_SUMMARY = ROOT / "burnin_summary.md"

ENDPOINT = "http://localhost:11434/v1"
# chat() appends /v1/chat/completions — base URL must be host only.
LLM_BASE_URL = "http://localhost:11434"
MODEL = "qwen3:4b-instruct"
TEMPERATURE = "0"
SEED = "42"
NUM_CTX = "16384"
STREAM = "false"
PER_RUN_TIMEOUT_S = 900

FIELDNAMES = [
    "run_id",
    "label",
    "endpoint",
    "model",
    "baseline_model",
    "candidate_model",
    "temperature",
    "seed",
    "num_ctx",
    "stream",
    "status",
    "baseline_tool_count",
    "candidate_tool_count",
    "severity",
    "rule_ids",
    "wall_clock_s",
    "harness_fault",
    "exit_code",
    "transport_log",
]

RULE_IDS = [f"D{i:02d}" for i in range(1, 13)]


def _load_completed(csv_path: Path) -> dict[str, dict[str, str]]:
    if not csv_path.is_file():
        return {}
    with csv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        rid = (row.get("run_id") or "").strip()
        status = (row.get("status") or "").strip()
        if rid and status in {"completed", "HARNESS_FAULT", "operational_error"}:
            out[rid] = row
    return out


def _write_csv(csv_path: Path, rows: list[dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(csv_path)


def _trim_log(text: str, *, limit: int = 8000) -> str:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(cleaned) > limit:
        return cleaned[-limit:]
    return cleaned


def _parse_report(stdout: str) -> dict[str, object] | None:
    text = (stdout or "").strip()
    if not text:
        return None
    # vern --json may mix stderr-style lines if captured together; find JSON object.
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        # Try last JSON-looking line
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return None


def _run_one(
    *,
    entry: dict[str, object],
    runs_dir: Path,
    diff_out: Path,
    env: dict[str, str],
) -> dict[str, str]:
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

    cmd = [
        "uv",
        "run",
        "vern",
        "diff",
        run_id,
        "--model",
        MODEL,
        "--runs-dir",
        str(runs_dir),
        "--out",
        str(diff_out),
        "--json",
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
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else (
            exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        )
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else (
            exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        )
        proc_rc = -1
        combined = stderr + "\n" + stdout + f"\nTIMEOUT after {PER_RUN_TIMEOUT_S}s"
    else:
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        proc_rc = proc.returncode
        combined = stderr + "\n" + stdout

    wall = f"{time.perf_counter() - t0:.3f}"
    transport = _trim_log(combined)

    row: dict[str, str] = {
        "run_id": run_id,
        "label": label,
        "endpoint": ENDPOINT,
        "model": MODEL,
        "baseline_model": MODEL,
        "candidate_model": MODEL,
        "temperature": TEMPERATURE,
        "seed": SEED,
        "num_ctx": NUM_CTX,
        "stream": STREAM,
        "status": "operational_error",
        "baseline_tool_count": "",
        "candidate_tool_count": "",
        "severity": "",
        "rule_ids": "",
        "wall_clock_s": wall,
        "harness_fault": "",
        "exit_code": str(proc_rc if not timed_out else -1),
        "transport_log": transport,
    }

    if timed_out:
        row["status"] = "operational_error"
        row["severity"] = "OPS_TIMEOUT"
        return row

    # Tool counts from projected trajectories when dirs exist.
    base_tools = tool_call_count(project_run(dest)) if dest.is_dir() else 0
    row["baseline_tool_count"] = str(base_tools)

    report = _parse_report(stdout)
    cand_dir: Path | None = None
    if report and isinstance(report.get("candidate_run_id"), str):
        cand_dir = diff_out / str(report["candidate_run_id"])
    if cand_dir is not None and cand_dir.is_dir():
        row["candidate_tool_count"] = str(tool_call_count(project_run(cand_dir)))
    else:
        row["candidate_tool_count"] = "0"

    if report and isinstance(report.get("verdict"), dict):
        verdict = report["verdict"]  # type: ignore[assignment]
        assert isinstance(verdict, dict)
        hf = verdict.get("harness_fault")
        if isinstance(hf, str) and hf:
            row["status"] = "HARNESS_FAULT"
            row["harness_fault"] = hf
            row["severity"] = "HARNESS_FAULT"
            row["rule_ids"] = ""
        else:
            row["status"] = "completed"
            row["severity"] = str(verdict.get("severity") or "")
            rules = verdict.get("rule_ids") or []
            if isinstance(rules, list):
                row["rule_ids"] = "|".join(str(r) for r in rules)
            else:
                row["rule_ids"] = str(rules)
    elif proc_rc == 2:
        row["status"] = "operational_error"
        row["severity"] = "OPS_ERROR"
    else:
        # Diff returned but JSON missing — still operational unless we got a verdict.
        row["status"] = "operational_error"
        row["severity"] = "PARSE_ERROR"

    return row


def _write_summary(path: Path, rows: list[dict[str, str]]) -> None:
    completed = [r for r in rows if r.get("status") == "completed"]
    zero_tool = [
        r
        for r in rows
        if (r.get("candidate_tool_count") or "0") == "0"
        and (r.get("baseline_tool_count") or "0") not in {"", "0"}
        and int(r.get("baseline_tool_count") or "0") > 0
    ]
    sev_counts: Counter[str] = Counter()
    for r in rows:
        sev = (r.get("severity") or "").strip() or "(empty)"
        sev_counts[sev] += 1

    rule_hist: Counter[str] = Counter({rid: 0 for rid in RULE_IDS})
    for r in rows:
        raw = (r.get("rule_ids") or "").strip()
        if not raw:
            continue
        for rid in raw.split("|"):
            rid = rid.strip()
            if rid in rule_hist:
                rule_hist[rid] += 1
            elif rid:
                rule_hist[rid] += 1

    lines = [
        "# Burn-in summary",
        "",
        f"- completed: {len(completed)}/30",
        f"- zero-tool collapses (candidate_tool_count==0 while baseline_tool_count>0): "
        f"{len(zero_tool)}",
        "",
        "## Severity distribution",
        "",
    ]
    for sev, n in sorted(sev_counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {sev}: {n}")
    lines.extend(["", "## Rule-ID histogram (D01–D12)", ""])
    for rid in RULE_IDS:
        lines.append(f"- {rid}: {rule_hist.get(rid, 0)}")
    # Any unexpected rule ids
    extras = sorted(k for k in rule_hist if k not in RULE_IDS)
    for rid in extras:
        lines.append(f"- {rid}: {rule_hist[rid]}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip run_ids already present in burnin.csv",
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args(argv)

    if not MANIFEST.is_file():
        print("missing MANIFEST.json — run scripts/build_corpus.py first", file=sys.stderr)
        return 2

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = list(data["runs"])
    if len(entries) != 30:
        print(f"expected 30 corpus runs, found {len(entries)}", file=sys.stderr)
        return 2

    completed_map = _load_completed(args.csv) if args.resume else {}
    rows: list[dict[str, str]] = []
    if args.resume:
        # Preserve prior row order for completed ids, then append new.
        seen: set[str] = set()
        if args.csv.is_file():
            with args.csv.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    rid = (row.get("run_id") or "").strip()
                    if rid in completed_map and rid not in seen:
                        rows.append(completed_map[rid])
                        seen.add(rid)

    staging = ROOT / "testdata" / "corpus" / "_burnin_staging"
    runs_dir = staging / "runs"
    diff_out = staging / "diff-runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    diff_out.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["VERNIER_LLM_BASE_URL"] = LLM_BASE_URL
    env["OPENAI_BASE_URL"] = LLM_BASE_URL
    env.setdefault("OPENAI_API_KEY", "ollama")
    env.setdefault("VERNIER_LLM_API_KEY", "ollama")
    env["VERNIER_LLM_TEMPERATURE"] = TEMPERATURE
    env["VERNIER_LLM_SEED"] = SEED
    env["VERNIER_LLM_NUM_CTX"] = NUM_CTX
    env["VERNIER_LLM_STREAM"] = STREAM
    env.setdefault("VERNIER_LLM_TIMEOUT_S", "600")

    print(
        f"burn-in: endpoint={ENDPOINT} model={MODEL} "
        f"temperature={TEMPERATURE} seed={SEED} num_ctx={NUM_CTX} stream={STREAM}",
        flush=True,
    )
    print(
        f"csv={args.csv} resume={args.resume} skip={len(completed_map)}",
        flush=True,
    )

    t_all = time.perf_counter()
    zero_tool_failures: list[dict[str, str]] = []

    def _on_sigint(_signum: int, _frame: object) -> None:
        print("\nburn-in: interrupted; CSV checkpoint retained", flush=True)
        raise SystemExit(130)

    signal.signal(signal.SIGINT, _on_sigint)

    for i, entry in enumerate(entries, start=1):
        run_id = str(entry["run_id"])
        label = str(entry.get("label") or "")
        if args.resume and run_id in completed_map:
            print(f"run {i}/30: {label} {run_id} … skip (resume)", flush=True)
            continue

        print(f"run {i}/30: {label} {run_id} …", flush=True)
        row = _run_one(entry=entry, runs_dir=runs_dir, diff_out=diff_out, env=env)
        rows.append(row)
        _write_csv(args.csv, rows)

        elapsed = time.perf_counter() - t_all
        rules = row.get("rule_ids") or "-"
        print(
            f"  status={row['status']} severity={row.get('severity') or '-'} "
            f"tools={row.get('baseline_tool_count')}→{row.get('candidate_tool_count')} "
            f"rules={rules or '-'} wall={row['wall_clock_s']}s "
            f"elapsed={elapsed:.1f}s",
            flush=True,
        )

        try:
            btc = int(row.get("baseline_tool_count") or "0")
            ctc = int(row.get("candidate_tool_count") or "0")
        except ValueError:
            btc, ctc = 0, 0
        if ctc == 0 and btc > 0:
            zero_tool_failures.append(row)
            print(
                f"ZERO_TOOL: run_id={run_id}\n{row.get('transport_log', '')}",
                flush=True,
            )

    _write_csv(args.csv, rows)
    _write_summary(args.summary, rows)

    completed_n = sum(1 for r in rows if r.get("status") == "completed")
    print(f"wrote {args.csv} ({len(rows)} rows)", flush=True)
    print(f"wrote {args.summary} (completed {completed_n}/30)", flush=True)

    if zero_tool_failures:
        print(
            f"FAIL: {len(zero_tool_failures)} run(s) with "
            "candidate_tool_count==0 while baseline_tool_count>0",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
