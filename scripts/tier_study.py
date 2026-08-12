#!/usr/bin/env python3
"""Local small-model tier study (NOT KT-1).

Pairs (baseline always qwen3:4b-instruct from the recorded corpus):
  T1  qwen3:4b-instruct -> qwen3:4b-instruct  (null; seeds 42/43/44)
  T2  qwen3:4b-instruct -> llama3.2:3b        (tier down, cross-family)
  T3  qwen3:4b-instruct -> llama3.1:8b        (tier up, cross-family)

All 30 corpus runs × each (T1 × 3 seeds). temperature=0.
Does not write RESULTS.md. Emits tier_study.csv + tier_study.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from vernier.canonical._jcs import serialize_jcs
from vernier.record.store import RecordStore, load_run
from vernier.replay.harness_guard import tool_call_count
from vernier.replay.project import project_run
from vernier.replay.request_parity import request_options_from_body

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "testdata" / "corpus" / "MANIFEST.json"
DEFAULT_CSV = ROOT / "tier_study.csv"
DEFAULT_MD = ROOT / "tier_study.md"

ENDPOINT = "http://localhost:11434/v1"
LLM_BASE_URL = "http://localhost:11434"
BASELINE_MODEL = "qwen3:4b-instruct"
TEMPERATURE = "0"
STREAM = "false"
# Corpus recorded without Ollama ``options``; both legs must match (no num_ctx).
REQUEST_OPTIONS = "null"
PER_RUN_TIMEOUT_S = 900
RULE_IDS = [f"D{i:02d}" for i in range(1, 13)]

# T1 null pair uses three pinned seeds; T2/T3 use seed 42 only.
T1_SEEDS = ("42", "43", "44")
DEFAULT_SEED = "42"

PAIRS: list[dict[str, object]] = [
    {
        "pair_id": "T1",
        "label": "null (same model)",
        "candidate_model": BASELINE_MODEL,
        "seeds": T1_SEEDS,
    },
    {
        "pair_id": "T2",
        "label": "tier down, cross-family",
        "candidate_model": "llama3.2:3b",
        "seeds": (DEFAULT_SEED,),
    },
    {
        "pair_id": "T3",
        "label": "tier up, cross-family",
        "candidate_model": "llama3.1:8b",
        "seeds": (DEFAULT_SEED,),
    },
]

FIELDNAMES = [
    "pair_id",
    "pair_label",
    "run_id",
    "corpus_label",
    "agent_domain",
    "baseline_model",
    "candidate_model",
    "temperature",
    "seed",
    "request_options",
    "baseline_request_options",
    "candidate_request_options",
    "stream",
    "status",
    "candidate_run_id",
    "baseline_tool_count",
    "candidate_tool_count",
    "severity",
    "rule_ids",
    "harness_fault",
    "wall_clock_s",
    "exit_code",
]


def _domain(corpus_label: str) -> str:
    # research_01 -> research
    if "_" in corpus_label:
        return corpus_label.rsplit("_", 1)[0]
    return corpus_label or "unknown"


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95% default)."""
    if n <= 0:
        return (float("nan"), float("nan"))
    phat = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = phat + z2 / (2.0 * n)
    margin = z * math.sqrt((phat * (1.0 - phat) + z2 / (4.0 * n)) / n)
    low = (centre - margin) / denom
    high = (centre + margin) / denom
    return (max(0.0, low), min(1.0, high))


def _parse_report(stdout: str) -> dict[str, object] | None:
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


def _row_key(row: dict[str, str]) -> str:
    return f"{row['pair_id']}|{row['run_id']}|{row['seed']}|{row['candidate_model']}"


def _load_completed(csv_path: Path) -> dict[str, dict[str, str]]:
    if not csv_path.is_file():
        return {}
    out: dict[str, dict[str, str]] = {}
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            status = (row.get("status") or "").strip()
            if status in {"completed", "HARNESS_FAULT", "operational_error"}:
                out[_row_key(row)] = row
    return out


def _write_csv(csv_path: Path, rows: list[dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(csv_path)


def _first_request_options(run_dir: Path) -> str:
    if not run_dir.is_dir():
        return ""
    try:
        frozen = load_run(run_dir)
        reader = RecordStore(run_dir, frozen.run_id)
        for env in reader.read_events():
            if env["kind"] != "LlmRequest":
                continue
            payload = reader.read_payload(env)
            ref = payload.get("body_blob_ref")
            if not ref:
                return "(missing-body)"
            return request_options_from_body(reader.read_blob(ref))
    except (OSError, ValueError):
        return "(unreadable)"
    return "(no-request)"


def _run_one(
    *,
    pair_id: str,
    pair_label: str,
    candidate_model: str,
    seed: str,
    entry: dict[str, object],
    runs_dir: Path,
    diff_out: Path,
    env: dict[str, str],
) -> dict[str, str]:
    run_id = str(entry["run_id"])
    corpus_label = str(entry.get("label") or "")
    src = ROOT / "testdata" / "corpus" / str(entry["path"])
    dest = runs_dir / run_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    manifest_path = dest / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cwd"] = str(ROOT)
    manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")

    run_env = env.copy()
    run_env["VERNIER_LLM_SEED"] = seed
    run_env.pop("VERNIER_CONTROL_DROP_TOOL", None)

    cmd = [
        "uv",
        "run",
        "vern",
        "diff",
        run_id,
        "--model",
        candidate_model,
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
            env=run_env,
            timeout=PER_RUN_TIMEOUT_S,
        )
        timed_out = False
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        proc_rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (
            (exc.stdout or "")
            if isinstance(exc.stdout, str)
            else (exc.stdout.decode("utf-8", errors="replace") if exc.stdout else "")
        )
        stderr = (
            (exc.stderr or "")
            if isinstance(exc.stderr, str)
            else (exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "")
        )
        proc_rc = -1

    wall = f"{time.perf_counter() - t0:.3f}"
    row: dict[str, str] = {
        "pair_id": pair_id,
        "pair_label": pair_label,
        "run_id": run_id,
        "corpus_label": corpus_label,
        "agent_domain": _domain(corpus_label),
        "baseline_model": BASELINE_MODEL,
        "candidate_model": candidate_model,
        "temperature": TEMPERATURE,
        "seed": seed,
        "request_options": REQUEST_OPTIONS,
        "baseline_request_options": "",
        "candidate_request_options": "",
        "stream": STREAM,
        "status": "operational_error",
        "candidate_run_id": "",
        "baseline_tool_count": "",
        "candidate_tool_count": "",
        "severity": "",
        "rule_ids": "",
        "harness_fault": "",
        "wall_clock_s": wall,
        "exit_code": str(proc_rc if not timed_out else -1),
    }

    if timed_out:
        row["severity"] = "OPS_TIMEOUT"
        return row

    base_tools = tool_call_count(project_run(dest)) if dest.is_dir() else 0
    row["baseline_tool_count"] = str(base_tools)

    report = _parse_report(stdout)
    cand_dir: Path | None = None
    if report and isinstance(report.get("candidate_run_id"), str):
        row["candidate_run_id"] = str(report["candidate_run_id"])
        cand_dir = diff_out / str(report["candidate_run_id"])
    row["baseline_request_options"] = _first_request_options(dest)
    if cand_dir is not None and cand_dir.is_dir():
        row["candidate_tool_count"] = str(tool_call_count(project_run(cand_dir)))
        row["candidate_request_options"] = _first_request_options(cand_dir)
    else:
        row["candidate_tool_count"] = "0"
        row["candidate_request_options"] = ""

    if report and isinstance(report.get("verdict"), dict):
        verdict = report["verdict"]
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
        row["severity"] = "OPS_ERROR"
    else:
        row["severity"] = "PARSE_ERROR"

    return row


def _is_divergence(row: dict[str, str]) -> bool:
    """HARNESS_FAULT and ops errors are never counted as divergences."""
    if row.get("status") != "completed":
        return False
    sev = (row.get("severity") or "").strip()
    return sev not in {"", "NONE"}


def _emit_md(path: Path, rows: list[dict[str, str]]) -> None:
    by_pair: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_pair[r["pair_id"]].append(r)

    # Null rate from all T1 completed (non-fault) comparisons.
    t1_rows = by_pair.get("T1", [])
    t1_scored = [r for r in t1_rows if r.get("status") == "completed"]
    t1_div = sum(1 for r in t1_scored if _is_divergence(r))
    t1_n = len(t1_scored)
    t1_rate = (t1_div / t1_n) if t1_n else float("nan")

    lines: list[str] = [
        "# Local small-model tier study (NOT KT-1)",
        "",
        "This is a **local tier study**, not the hosted KT-1 upgrade pair.",
        "",
        f"- Baseline (recorded corpus): `{BASELINE_MODEL}`",
        f"- Endpoint: `{ENDPOINT}`",
        f"- temperature=`{TEMPERATURE}`, stream=`{STREAM}`, "
        f"request_options=`{REQUEST_OPTIONS}` (no num_ctx on either leg)",
        f"- Corpus: 30 runs under `testdata/corpus/`",
        f"- T1 null seeds: {', '.join(T1_SEEDS)}",
        f"- T2/T3 seed: `{DEFAULT_SEED}`",
        "",
        "Divergence = completed run with severity ≠ NONE. "
        "**HARNESS_FAULT is reported separately and never counted as a divergence.**",
        "",
        "## Headline net rates",
        "",
        "NET = pair divergence rate − T1 null rate. "
        "Headline figures are **net** (except T1, which *is* the null rate).",
        "",
        "| Pair | Candidate | Raw div | N scored | Raw rate | Wilson 95% CI | "
        "NET vs T1 | HARNESS_FAULT |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |",
    ]

    pair_stats: dict[str, dict[str, object]] = {}
    for spec in PAIRS:
        pid = str(spec["pair_id"])
        prow = by_pair.get(pid, [])
        scored = [r for r in prow if r.get("status") == "completed"]
        faults = [r for r in prow if r.get("status") == "HARNESS_FAULT"]
        ops = [r for r in prow if r.get("status") == "operational_error"]
        k = sum(1 for r in scored if _is_divergence(r))
        n = len(scored)
        rate = (k / n) if n else float("nan")
        lo, hi = _wilson_ci(k, n)
        if pid == "T1":
            net = rate  # null finding stands as-is
            net_s = f"{rate:.4f} (null)"
        else:
            net = rate - t1_rate if n and t1_n else float("nan")
            net_s = f"{net:+.4f}"
        pair_stats[pid] = {
            "k": k,
            "n": n,
            "rate": rate,
            "lo": lo,
            "hi": hi,
            "net": net,
            "faults": len(faults),
            "ops": len(ops),
            "total": len(prow),
        }
        cand = str(spec["candidate_model"])
        ci = f"[{lo:.4f}, {hi:.4f}]" if n else "n/a"
        rate_s = f"{rate:.4f}" if n else "n/a"
        lines.append(
            f"| {pid} | `{cand}` | {k} | {n} | {rate_s} | {ci} | {net_s} | "
            f"{len(faults)} |"
        )

    lines.extend(
        [
            "",
            f"T1 null rate (denominator for NET): **{t1_div}/{t1_n} = "
            f"{t1_rate:.4f}**"
            if t1_n
            else "T1 null rate: n/a",
            "",
        ]
    )

    # Per-pair detail
    for spec in PAIRS:
        pid = str(spec["pair_id"])
        prow = by_pair.get(pid, [])
        scored = [r for r in prow if r.get("status") == "completed"]
        faults = [r for r in prow if r.get("status") == "HARNESS_FAULT"]
        st = pair_stats[pid]
        lines.extend(
            [
                f"## {pid} — {spec['label']}",
                "",
                f"- candidate: `{spec['candidate_model']}`",
                f"- seeds: {', '.join(str(s) for s in spec['seeds'])}",  # type: ignore[arg-type]
                f"- comparisons: {st['total']} "
                f"(scored={st['n']}, HARNESS_FAULT={st['faults']}, "
                f"ops_error={st['ops']})",
                f"- raw divergence: {st['k']}/{st['n']} = {st['rate']:.4f}"
                if st["n"]
                else "- raw divergence: n/a",
                f"- Wilson 95% CI: [{st['lo']:.4f}, {st['hi']:.4f}]"
                if st["n"]
                else "- Wilson 95% CI: n/a",
                f"- **NET vs T1:** {st['net']:+.4f}"
                if pid != "T1" and st["n"] and t1_n
                else (
                    f"- **null rate (finding):** {st['rate']:.4f}"
                    if pid == "T1" and st["n"]
                    else "- NET: n/a"
                ),
                f"- HARNESS_FAULT count: **{st['faults']}** "
                f"(not counted as divergences)",
                "",
                "### Rule-ID histogram (D01–D12)",
                "",
            ]
        )
        hist: Counter[str] = Counter({rid: 0 for rid in RULE_IDS})
        for r in scored:
            if not _is_divergence(r):
                continue
            for rid in (r.get("rule_ids") or "").split("|"):
                rid = rid.strip()
                if rid:
                    hist[rid] += 1
        for rid in RULE_IDS:
            lines.append(f"- {rid}: {hist.get(rid, 0)}")
        extras = sorted(k for k in hist if k not in RULE_IDS)
        for rid in extras:
            lines.append(f"- {rid}: {hist[rid]}")

        lines.extend(["", "### Per-agent-domain divergence", ""])
        lines.append("| Domain | Diverged | Scored | Rate |")
        lines.append("| --- | ---: | ---: | ---: |")
        by_dom: dict[str, list[dict[str, str]]] = defaultdict(list)
        for r in scored:
            by_dom[r.get("agent_domain") or "unknown"].append(r)
        for dom in sorted(by_dom):
            rs = by_dom[dom]
            dk = sum(1 for r in rs if _is_divergence(r))
            dn = len(rs)
            lines.append(f"| {dom} | {dk} | {dn} | {dk / dn:.4f} |")

        if faults:
            lines.extend(
                [
                    "",
                    f"### HARNESS_FAULT detail ({len(faults)})",
                    "",
                ]
            )
            fault_reasons: Counter[str] = Counter(
                (r.get("harness_fault") or "(empty)") for r in faults
            )
            for reason, n in fault_reasons.most_common():
                lines.append(f"- `{reason}`: {n}")
        lines.append("")

    lines.extend(
        [
            "## Method notes",
            "",
            "- Live-diff via `vern diff --model <candidate>`; baseline events "
            "are the recorded corpus trajectory.",
            "- Request options matched: neither leg sends `options.num_ctx` "
            f"(recorded as `{REQUEST_OPTIONS}`).",
            "- Harness aborts with HARNESS_FAULT if control fields "
            "(temperature/stream/options/tools) differ from baseline before "
            "the model is called.",
            "- HARNESS_FAULT rows are excluded from divergence numerators and "
            "denominators (N scored = completed only).",
            "- No classifier tuning was performed for this study.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--md", type=Path, default=DEFAULT_MD)
    parser.add_argument(
        "--pairs",
        default="T1,T2,T3",
        help="Comma-separated pair ids to run (default: all).",
    )
    args = parser.parse_args(argv)

    if not MANIFEST.is_file():
        print("missing MANIFEST.json — rebuild corpus first", file=sys.stderr)
        return 2
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = list(data["runs"])
    if len(entries) != 30:
        print(f"expected 30 corpus runs, found {len(entries)}", file=sys.stderr)
        return 2

    want = {p.strip() for p in args.pairs.split(",") if p.strip()}
    specs = [p for p in PAIRS if p["pair_id"] in want]
    if not specs:
        print(f"no pairs selected from {want}", file=sys.stderr)
        return 2

    completed_map = _load_completed(args.csv) if args.resume else {}
    rows: list[dict[str, str]] = []
    if args.resume and args.csv.is_file():
        seen: set[str] = set()
        with args.csv.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                key = _row_key(row)
                if key in completed_map and key not in seen:
                    rows.append(completed_map[key])
                    seen.add(key)

    staging = ROOT / "testdata" / "corpus" / "_tier_study_staging"
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
    env["VERNIER_LLM_STREAM"] = STREAM
    env.pop("VERNIER_LLM_NUM_CTX", None)
    env.setdefault("VERNIER_LLM_TIMEOUT_S", "600")
    env.pop("VERNIER_CONTROL_DROP_TOOL", None)

    jobs: list[tuple[dict[str, object], dict[str, object], str]] = []
    for spec in specs:
        for seed in spec["seeds"]:  # type: ignore[attr-defined]
            for entry in entries:
                jobs.append((spec, entry, str(seed)))

    total = len(jobs)
    print(
        f"tier study (NOT KT-1): baseline={BASELINE_MODEL} "
        f"jobs={total} resume={args.resume} skip={len(completed_map)}",
        flush=True,
    )

    t_all = time.perf_counter()
    done = 0
    for spec, entry, seed in jobs:
        pair_id = str(spec["pair_id"])
        cand = str(spec["candidate_model"])
        probe = {
            "pair_id": pair_id,
            "run_id": str(entry["run_id"]),
            "seed": seed,
            "candidate_model": cand,
        }
        key = _row_key(probe)
        if args.resume and key in completed_map:
            done += 1
            continue
        done += 1
        label = str(entry.get("label") or "")
        print(
            f"[{done}/{total}] {pair_id} seed={seed} {label} {entry['run_id']} "
            f"-> {cand} …",
            flush=True,
        )
        row = _run_one(
            pair_id=pair_id,
            pair_label=str(spec["label"]),
            candidate_model=cand,
            seed=seed,
            entry=entry,
            runs_dir=runs_dir,
            diff_out=diff_out,
            env=env,
        )
        rows.append(row)
        _write_csv(args.csv, rows)
        div = "DIV" if _is_divergence(row) else row.get("status", "")
        print(
            f"  status={row['status']} severity={row['severity']} "
            f"tools={row['baseline_tool_count']}→{row['candidate_tool_count']} "
            f"rules={row['rule_ids'] or '-'} flag={div} "
            f"wall={row['wall_clock_s']}s",
            flush=True,
        )

    _write_csv(args.csv, rows)
    _emit_md(args.md, rows)
    print(
        f"wrote {args.csv} ({len(rows)} rows) and {args.md} "
        f"in {time.perf_counter() - t_all:.1f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
