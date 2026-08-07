#!/usr/bin/env python3
"""Build the KT-1 evaluation corpus (≥30 runs across 6 agent shapes).

Records against the designated baseline model ``vernier-baseline-local-v1``
(local deterministic stub — no paid API required for corpus construction).

Raw run payloads live under ``testdata/corpus/runs/`` and are gitignored;
``testdata/corpus/MANIFEST.json`` is committed. Rebuild anytime with::

    uv run python scripts/build_corpus.py
    uv run python scripts/verify_corpus.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from vernier.canonical._jcs import serialize_jcs
from vernier.record.store import load_run
from vernier.replay.project import project_run

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "testdata" / "corpus"
RUNS = OUT / "runs"
BASELINE_MODEL = "vernier-baseline-local-v1"

AGENTS: dict[str, dict[str, object]] = {
    "research": {
        "script": "examples/research_agent.py",
        "inputs": ["climate", "antibiotics", "llms", "urban-heat", "oceans"],
    },
    "data": {
        "script": "examples/data_agent.py",
        "inputs": ["sales", "inventory", "users", "events", "metrics"],
    },
    "support": {
        "script": "examples/support_agent.py",
        "inputs": [
            "cust1:t9",
            "cust2:t3",
            "cust1:t1",
            "cust9:t2",
            "cust3:t8",
        ],
    },
    "coding": {
        "script": "examples/coding_agent.py",
        "inputs": ["payments", "auth", "cache", "parser", "scheduler"],
    },
    "booking": {
        "script": "examples/booking_agent.py",
        "inputs": [
            "SFO-JFK:ada",
            "LAX-SEA:bob",
            "ORD-MIA:cara",
            "DEN-BOS:dan",
            "ATL-DFW:eve",
        ],
    },
    "router": {
        "script": "examples/router_agent.py",
        "inputs": [
            "weather Paris",
            "math 42",
            "search quantum dots",
            "weather Tokyo",
            "math 7",
        ],
    },
}


def _record(script: str, input_value: str, tmp_runs: Path) -> Path:
    if tmp_runs.exists():
        shutil.rmtree(tmp_runs)
    tmp_runs.mkdir(parents=True)
    cmd = [
        "uv",
        "run",
        "vern",
        "record",
        "--out",
        str(tmp_runs),
        "--",
        sys.executable,
        str(ROOT / script),
        "--input",
        input_value,
        "--model",
        BASELINE_MODEL,
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    dirs = [p for p in tmp_runs.iterdir() if p.is_dir()]
    if not dirs:
        raise RuntimeError(
            f"record failed for {script} input={input_value}:\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
    return dirs[0]


def main() -> int:
    # Keep README; rebuild runs + manifest.
    if RUNS.exists():
        shutil.rmtree(RUNS)
    OUT.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, object]] = []
    tmp = OUT / "_tmp_record"

    for shape, cfg in AGENTS.items():
        script = str(cfg["script"])
        inputs = list(cfg["inputs"])  # type: ignore[arg-type]
        for index, input_value in enumerate(inputs, start=1):
            label = f"{shape}_{index:02d}"
            print(f"recording {label} input={input_value!r} ...", flush=True)
            src = _record(script, input_value, tmp)
            frozen = load_run(src)
            dest = RUNS / frozen.run_id
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)

            portable = [
                "python",
                script,
                "--input",
                input_value,
                "--model",
                BASELINE_MODEL,
            ]
            manifest_path = dest / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["command"] = portable
            manifest["cwd"] = str(ROOT)
            manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")

            traj = project_run(dest)
            if len(traj) < 8:
                raise RuntimeError(
                    f"{label} projected only {len(traj)} steps; need >=8"
                )

            entries.append(
                {
                    "run_id": frozen.run_id,
                    "shape": shape,
                    "label": label,
                    "script": script,
                    "input": input_value,
                    "baseline_model": BASELINE_MODEL,
                    "event_count": frozen.manifest.event_count,
                    "projected_steps": len(traj),
                    "path": f"runs/{frozen.run_id}",
                }
            )
            print(
                f"  OK {label} run_id={frozen.run_id} "
                f"steps={len(traj)} events={frozen.manifest.event_count}",
                flush=True,
            )

    if tmp.exists():
        shutil.rmtree(tmp)

    corpus_manifest = {
        "version": "corpus-v1",
        "baseline_model": BASELINE_MODEL,
        "run_count": len(entries),
        "shapes": sorted(AGENTS),
        "storage": {
            "runs_dir": "runs/",
            "git": "raw records gitignored; rebuild via scripts/build_corpus.py",
            "external_bucket": os.environ.get(
                "VERNIER_CORPUS_BUCKET",
                "(optional) set VERNIER_CORPUS_BUCKET to sync elsewhere",
            ),
        },
        "runs": entries,
    }
    (OUT / "MANIFEST.json").write_text(
        json.dumps(corpus_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT / 'MANIFEST.json'} with {len(entries)} runs", flush=True)
    if len(entries) < 30:
        print("ERROR: need >=30 runs", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
