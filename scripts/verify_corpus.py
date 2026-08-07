#!/usr/bin/env python3
"""Verify every corpus run at severity NONE (KT-3 on real corpus)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from vernier.canonical._jcs import serialize_jcs

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "testdata" / "corpus" / "MANIFEST.json"


def main() -> int:
    if not MANIFEST.is_file():
        print("missing MANIFEST.json — run scripts/build_corpus.py first", flush=True)
        return 2
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    runs = data["runs"]
    failed = 0
    for entry in runs:
        run_id = entry["run_id"]
        src = ROOT / "testdata" / "corpus" / entry["path"]
        if not src.is_dir():
            print(f"MISSING {run_id}", flush=True)
            failed += 1
            continue
        # Stage into a flat runs dir for vern verify.
        staging = ROOT / "testdata" / "corpus" / "_verify_staging" / "runs"
        staging.mkdir(parents=True, exist_ok=True)
        dest = staging / run_id
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        # Ensure portable command + cwd.
        manifest_path = dest / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["cwd"] = str(ROOT)
        manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")

        proc = subprocess.run(
            [
                "uv",
                "run",
                "vern",
                "verify",
                run_id,
                "--runs-dir",
                str(staging),
                "--out",
                str(ROOT / "testdata" / "corpus" / "_verify_staging" / "verify-runs"),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            failed += 1
            print(f"FAIL {entry['label']} {run_id}", flush=True)
            print(proc.stderr[-1500:], flush=True)
        else:
            print(f"OK   {entry['label']} {run_id}", flush=True)

    print(f"verified {len(runs) - failed}/{len(runs)}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
