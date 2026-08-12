#!/usr/bin/env python3
"""Emit GitHub Actions step outputs from a vernier-gate-report-v1 JSON file."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: emit_outputs.py <gate-report.json>", file=sys.stderr)
        return 2
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    worst = report.get("worst_severity") or "NONE"
    rules = ",".join(report.get("rule_ids") or [])
    n = report.get("n_runs") or 0
    # GITHUB_OUTPUT key=value lines (caller redirects).
    print(f"worst-severity={worst}")
    print(f"rule-ids={rules}")
    print(f"n-runs={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
