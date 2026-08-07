#!/usr/bin/env python3
"""KT-1 experiment driver: vern diff each corpus run against candidate models.

Emits CSV: run_id,baseline_model,candidate_model,severity,rule_ids

Budget guard: hard-cap total spend at $100. Prints running cost. Aborts at cap.

Cost model (approximate, documented):
  - Estimates tokens from recorded LlmRequest/LlmResponse body sizes.
  - Prices default to conservative mid-tier chat rates (override via env).
  - Dry-run (default when no ``VERNIER_KT1_LIVE=1``): still runs ``vern diff``
    against the local baseline stub with ``--model`` override only, so spend
    stays near $0 while exercising the pipeline.

Env:
  VERNIER_KT1_CANDIDATES   comma-separated model ids (default: two locals)
  VERNIER_KT1_BUDGET_USD   default 100
  VERNIER_KT1_LIVE         set to 1 to bill as live paid providers
  VERNIER_KT1_INPUT_PER_1K USD per 1K input tokens (default 0.50)
  VERNIER_KT1_OUTPUT_PER_1K USD per 1K output tokens (default 1.50)
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from vernier.canonical._jcs import serialize_jcs
from vernier.record.store import RecordStore, load_run

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "testdata" / "corpus" / "MANIFEST.json"
DEFAULT_BUDGET = 100.0


def _candidates() -> list[str]:
    raw = os.environ.get(
        "VERNIER_KT1_CANDIDATES",
        "vernier-candidate-a,vernier-candidate-b",
    )
    return [c.strip() for c in raw.split(",") if c.strip()]


def _estimate_tokens(run_dir: Path) -> tuple[int, int]:
    frozen = load_run(run_dir)
    reader = RecordStore(frozen.root, frozen.run_id)
    inp = 0
    out = 0
    for env in reader.read_events():
        kind = env["kind"]
        payload = reader.read_payload(env)
        size = int(payload.get("body_size") or 0)
        # ~4 chars/token rough estimate from body bytes.
        tokens = max(1, size // 4) if size else 0
        if kind == "LlmRequest":
            inp += tokens
        elif kind == "LlmResponse":
            out += tokens
    return inp, out


def _cost_usd(input_tokens: int, output_tokens: int) -> float:
    if os.environ.get("VERNIER_KT1_LIVE") != "1":
        # Local stub / dry path: treat as $0.00 for budget accounting.
        return 0.0
    in_rate = float(os.environ.get("VERNIER_KT1_INPUT_PER_1K", "0.50"))
    out_rate = float(os.environ.get("VERNIER_KT1_OUTPUT_PER_1K", "1.50"))
    return (input_tokens / 1000.0) * in_rate + (output_tokens / 1000.0) * out_rate


def main() -> int:
    if not MANIFEST.is_file():
        print(
            "missing MANIFEST.json — run scripts/build_corpus.py first", file=sys.stderr
        )
        return 2

    budget = float(os.environ.get("VERNIER_KT1_BUDGET_USD", str(DEFAULT_BUDGET)))
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    baseline = data["baseline_model"]
    candidates = _candidates()
    out_csv = Path(
        os.environ.get(
            "VERNIER_KT1_CSV",
            str(ROOT / "testdata" / "corpus" / "kt1_results.csv"),
        )
    )

    spent = 0.0
    rows: list[dict[str, str]] = []
    staging_root = ROOT / "testdata" / "corpus" / "_kt1_staging"
    if staging_root.exists():
        shutil.rmtree(staging_root)
    runs_dir = staging_root / "runs"
    runs_dir.mkdir(parents=True)

    print(f"KT-1 budget cap: ${budget:.2f}", flush=True)
    print(f"candidates: {candidates}", flush=True)
    print(f"live billing: {os.environ.get('VERNIER_KT1_LIVE') == '1'}", flush=True)

    aborted = False
    for entry in data["runs"]:
        if aborted:
            break
        run_id = entry["run_id"]
        src = ROOT / "testdata" / "corpus" / entry["path"]
        dest = runs_dir / run_id
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        manifest_path = dest / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["cwd"] = str(ROOT)
        manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")

        inp_tok, out_tok = _estimate_tokens(dest)
        unit_cost = _cost_usd(inp_tok, out_tok)

        for candidate in candidates:
            projected = spent + unit_cost
            if projected > budget + 1e-9:
                print(
                    f"ABORT: projected spend ${projected:.4f} exceeds "
                    f"cap ${budget:.2f} (running ${spent:.4f})",
                    flush=True,
                )
                aborted = True
                break

            print(
                f"diff {entry['label']} -> {candidate} "
                f"(est +${unit_cost:.4f}, running ${spent:.4f})",
                flush=True,
            )
            proc = subprocess.run(
                [
                    "uv",
                    "run",
                    "vern",
                    "diff",
                    run_id,
                    "--model",
                    candidate,
                    "--runs-dir",
                    str(runs_dir),
                    "--out",
                    str(staging_root / "diff-runs"),
                    "--json",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            spent = projected
            print(f"  running cost: ${spent:.4f} / ${budget:.2f}", flush=True)

            severity = "ERROR"
            rule_ids = ""
            if proc.returncode in {0, 1} and proc.stdout.strip():
                try:
                    report = json.loads(proc.stdout)
                    severity = report["verdict"]["severity"]
                    rule_ids = "|".join(report["verdict"].get("rule_ids") or [])
                except json.JSONDecodeError:
                    severity = "PARSE_ERROR"
            elif proc.returncode == 2:
                severity = "OPS_ERROR"

            rows.append(
                {
                    "run_id": run_id,
                    "baseline_model": baseline,
                    "candidate_model": candidate,
                    "severity": severity,
                    "rule_ids": rule_ids,
                }
            )

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "run_id",
                "baseline_model",
                "candidate_model",
                "severity",
                "rule_ids",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {out_csv} ({len(rows)} rows)", flush=True)
    print(f"final spend: ${spent:.4f} / ${budget:.2f}", flush=True)
    if aborted and not rows:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
