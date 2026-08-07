#!/usr/bin/env python3
"""Coding agent: read -> edit -> test (>=8 steps, >=4 tools)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from _corpus_llm import BASELINE_MODEL, chat, start_baseline_server  # noqa: E402

from vern import tool  # noqa: E402


@tool
def read_file(path: str) -> dict[str, str]:
    return {"path": path, "content": f"def {path.replace('.', '_')}():\n    return 1\n"}


@tool
def edit_file(path: str, patch: str) -> dict[str, str]:
    return {"path": path, "patch": patch, "status": "edited"}


@tool
def run_tests(target: str) -> dict[str, object]:
    return {"target": target, "passed": True, "failed": 0}


@tool
def git_status(path: str) -> dict[str, object]:
    return {"path": path, "dirty": True, "files": [path]}


def run(module: str) -> int:
    path = f"{module}.py"
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=10.0) as client:
            chat(client, base, f"fix bug in {module}")
            read_file(path)
            chat(client, base, f"propose patch for {module}")
            edit_file(path, f"fix:{module}")
            git_status(path)
            chat(client, base, f"test {module}")
            run_tests(module)
            read_file(path)
            chat(client, base, f"confirm fix {module}")
            run_tests(f"{module}::edge")
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="payments")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    del args.model
    return run(args.input)


if __name__ == "__main__":
    raise SystemExit(main())
