#!/usr/bin/env python3
"""Build a small vernier-corpus/ from verify fixtures for CI demos."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from vernier.canonical._jcs import serialize_jcs
from vernier.record.store import load_run

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "testdata" / "verify"
AGENT = ROOT / "examples" / "verify_fixture_agent.py"


def _prepare(case: Path, dest_run: Path, *, cwd: Path, agent: Path) -> None:
    if dest_run.exists():
        shutil.rmtree(dest_run)
    shutil.copytree(case / "run", dest_run)
    meta = json.loads((case / "meta.json").read_text(encoding="utf-8"))
    scenario = meta["scenario"]
    manifest_path = dest_run / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        agent_rel = str(agent.resolve().relative_to(cwd.resolve()))
    except ValueError:
        agent_rel = str(agent.resolve())
    manifest["command"] = ["python", agent_rel, "--scenario", scenario]
    manifest["cwd"] = str(cwd.resolve())
    manifest_path.write_bytes(serialize_jcs(manifest) + b"\n")
    # Touch load to ensure integrity.
    load_run(dest_run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "testdata" / "action_demo" / "vernier-corpus",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=ROOT,
        help="cwd baked into manifests (demo repo root).",
    )
    parser.add_argument(
        "--agent",
        type=Path,
        default=AGENT,
        help="Agent script path recorded in manifests.",
    )
    parser.add_argument(
        "--cases",
        default="01_tools_basic,08_many_llm",
        help="Comma-separated fixture names under testdata/verify.",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name in [c.strip() for c in args.cases.split(",") if c.strip()]:
        case = FIXTURES / name
        if not case.is_dir():
            print(f"missing fixture {case}", file=sys.stderr)
            return 2
        label_dir = args.out / name
        label_dir.mkdir(parents=True, exist_ok=True)
        _prepare(case, label_dir / "run", cwd=args.cwd, agent=args.agent)
        print(f"wrote {label_dir / 'run'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
