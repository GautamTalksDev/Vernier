#!/usr/bin/env python3
"""Record the 10 KT-3 verify fixtures under testdata/verify/."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "examples" / "verify_fixture_agent.py"
OUT = ROOT / "testdata" / "verify"

SCENARIOS = [
    "01_tools_basic",
    "02_stream",
    "03_fail_http",
    "04_fail_tool",
    "05_nondet",
    "06_deep_tools",
    "07_mixed",
    "08_many_llm",
    "09_tool_heavy",
    "10_stream_tools",
]


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    for scenario in SCENARIOS:
        runs = OUT / "_tmp_runs"
        if runs.exists():
            shutil.rmtree(runs)
        runs.mkdir()
        env = os.environ.copy()
        env["VERNIER_FIXTURE_SCENARIO"] = scenario
        if scenario == "05_nondet":
            env["VERNIER_CAPTURE_NONDET"] = "1"
            env["AGENT_FIXTURE_TOKEN"] = "fixture-token"
        cmd = [
            "uv",
            "run",
            "vern",
            "record",
            "--out",
            str(runs),
            "--",
            sys.executable,
            str(AGENT),
            "--scenario",
            scenario,
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        run_dirs = [p for p in runs.iterdir() if p.is_dir()]
        if not run_dirs:
            print(proc.stdout)
            print(proc.stderr)
            print(f"FAIL {scenario}: no run dir (code={proc.returncode})")
            return 1
        src = run_dirs[0]
        dest = OUT / scenario
        shutil.copytree(src, dest / "run")
        # Rewrite command to be repo-relative for portability.
        from vernier.canonical._jcs import serialize_jcs
        from vernier.record.store import load_run

        frozen = load_run(dest / "run")
        rel_agent = "examples/verify_fixture_agent.py"
        # Prefer portable argv: python + relative script when verified from repo.
        portable = ["python", rel_agent, "--scenario", scenario]
        manifest_path = dest / "run" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["command"] = portable
        manifest["cwd"] = str(ROOT)
        # Note: changing manifest without updating root_digest is OK for command
        # metadata; integrity of events is unchanged. Re-write with same root.
        manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")

        # Also patch RunStart payload command via rewriting is hard (would break
        # digests). verify falls back to manifest.command — good.

        meta = {
            "scenario": scenario,
            "original_exit": proc.returncode,
            "command": portable,
            "expect_fail": scenario.startswith("03_") or scenario.startswith("04_"),
        }
        (dest / "meta.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"OK {scenario} events={frozen.manifest.event_count} exit={proc.returncode}"
        )
        shutil.rmtree(runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
