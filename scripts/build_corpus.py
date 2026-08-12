#!/usr/bin/env python3
"""Build the KT-1 evaluation corpus (≥30 runs across 6 agent shapes).

Default baseline: ``vernier-baseline-local-v1`` (local stub with deterministic
tool_calls). For an Ollama-backed corpus (needed for burn-in self-pair)::

    VERNIER_LLM_BASE_URL=http://localhost:11434 \\
    VERNIER_CORPUS_MODEL=qwen3:4b-instruct \\
    uv run python scripts/build_corpus.py

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
from vernier.replay.harness_guard import tool_call_count
from vernier.replay.project import project_run

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "testdata" / "corpus"
RUNS = OUT / "runs"
BASELINE_MODEL = os.environ.get("VERNIER_CORPUS_MODEL", "vernier-baseline-local-v1")

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


def _record(script: str, input_value: str, tmp_runs: Path, *, model: str) -> Path:
    if tmp_runs.exists():
        shutil.rmtree(tmp_runs)
    tmp_runs.mkdir(parents=True)
    env = os.environ.copy()
    env.pop("VERNIER_CONTROL_DROP_TOOL", None)
    env.setdefault("OPENAI_API_KEY", "ollama")
    env.setdefault("VERNIER_LLM_API_KEY", "ollama")
    env.setdefault("VERNIER_LLM_TEMPERATURE", "0")
    env.setdefault("VERNIER_LLM_SEED", "42")
    env.setdefault("VERNIER_LLM_STREAM", "false")
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
        model,
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=env,
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
    model = BASELINE_MODEL
    print(f"building corpus with baseline_model={model!r}", flush=True)

    for shape, cfg in AGENTS.items():
        script = str(cfg["script"])
        inputs = list(cfg["inputs"])  # type: ignore[arg-type]
        for index, input_value in enumerate(inputs, start=1):
            label = f"{shape}_{index:02d}"
            print(f"recording {label} input={input_value!r} ...", flush=True)
            ok_dest: Path | None = None
            ok_traj = None
            ok_tools = 0
            last_err = ""
            for attempt in range(1, 4):
                src = _record(script, input_value, tmp, model=model)
                frozen_try = load_run(src)
                dest = RUNS / frozen_try.run_id
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)

                portable = [
                    "python",
                    script,
                    "--input",
                    input_value,
                    "--model",
                    model,
                ]
                manifest_path = dest / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["command"] = portable
                manifest["cwd"] = str(ROOT)
                manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")

                traj = project_run(dest)
                tools = tool_call_count(traj)
                ok_dest, ok_traj, ok_tools = dest, traj, tools
                if len(traj) >= 8 and tools >= 4:
                    break
                last_err = f"steps={len(traj)} tools={tools}"
                print(
                    f"  retry {attempt}/3 for {label} ({last_err})",
                    flush=True,
                )
            else:
                raise RuntimeError(
                    f"{label} failed >=8 steps and >=4 tools after retries "
                    f"({last_err})"
                )

            assert ok_dest is not None and ok_traj is not None
            frozen = load_run(ok_dest)

            entries.append(
                {
                    "run_id": frozen.run_id,
                    "shape": shape,
                    "label": label,
                    "script": script,
                    "input": input_value,
                    "baseline_model": model,
                    "event_count": frozen.manifest.event_count,
                    "projected_steps": len(ok_traj),
                    "tool_calls": ok_tools,
                    "path": f"runs/{frozen.run_id}",
                }
            )
            print(
                f"  OK {label} run_id={frozen.run_id} "
                f"steps={len(ok_traj)} tools={ok_tools} "
                f"events={frozen.manifest.event_count}",
                flush=True,
            )

    if tmp.exists():
        shutil.rmtree(tmp)

    corpus_manifest = {
        "version": "corpus-v1",
        "baseline_model": model,
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
    (OUT / "README.md").write_text(
        "# Vernier KT-1 corpus (model-driven agents)\n\n"
        "Prior scripted corpus: `../corpus-v0-invalid/` "
        "(see `docs/CORPUS-AUDIT.md`).\n\n"
        f"Baseline model: `{model}`\n"
        "Rebuild: `uv run python scripts/build_corpus.py`\n"
        "Verify (KT-3): `uv run python scripts/verify_corpus.py`\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT / 'MANIFEST.json'} with {len(entries)} runs", flush=True)
    if len(entries) < 30:
        print("ERROR: need >=30 runs", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
