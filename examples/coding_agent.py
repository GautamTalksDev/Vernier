#!/usr/bin/env python3
"""Coding agent: model-driven tool loop (>=4 tools, >=8 projected steps)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from _corpus_llm import (  # noqa: E402
    BASELINE_MODEL,
    http_timeout,
    run_tool_agent,
    start_baseline_server,
)

from vernier.capture import tool  # noqa: E402


@tool
def read_file(path: str) -> dict[str, str]:
    """Read a source file."""
    return {"path": path, "content": f"def {path.replace('.', '_')}():\n    return 1\n"}


@tool
def edit_file(path: str, patch: str) -> dict[str, str]:
    """Apply a patch to a source file."""
    return {"path": path, "patch": patch, "status": "edited"}


@tool
def run_tests(target: str) -> dict[str, object]:
    """Run tests for a target."""
    return {"target": target, "passed": True, "failed": 0}


@tool
def git_status(path: str) -> dict[str, object]:
    """Return git status for a path."""
    return {"path": path, "dirty": True, "files": [path]}


TOOLS = {
    "read_file": read_file,
    "edit_file": edit_file,
    "run_tests": run_tests,
    "git_status": git_status,
}

SYSTEM = (
    "You are a coding agent. Read code, edit it, check git status, and run tests. "
    "Use tools until the bug is fixed and tests pass."
)


def run(module: str, *, model: str = BASELINE_MODEL) -> int:
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=http_timeout()) as client:
            run_tool_agent(
                client,
                base,
                model=model,
                system=SYSTEM,
                user=(
                    f"Fix a bug in module {module!r} (file {module}.py). "
                    "Read the file, edit a patch, check git status, and run tests."
                ),
                tools=TOOLS,
            )
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="payments")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    return run(args.input, model=args.model)


if __name__ == "__main__":
    raise SystemExit(main())
