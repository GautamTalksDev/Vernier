#!/usr/bin/env python3
"""Data agent: model-driven tool loop (>=4 tools, >=8 projected steps)."""

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

from vern import tool  # noqa: E402


@tool
def sql_query(sql: str) -> dict[str, object]:
    """Run a SQL query and return rows."""
    return {"sql": sql, "rows": [{"id": 1, "v": 10}, {"id": 2, "v": 20}]}


@tool
def transform_rows(rows: list[dict[str, object]], op: str) -> list[dict[str, object]]:
    """Transform rows with an operation such as 'double'."""
    if op == "double":
        return [{"id": r["id"], "v": int(r["v"]) * 2} for r in rows]
    return list(rows)


@tool
def validate_schema(rows: list[dict[str, object]]) -> dict[str, object]:
    """Validate that rows have id and v fields."""
    return {"ok": all("id" in r and "v" in r for r in rows), "n": len(rows)}


@tool
def write_table(name: str, rows: list[dict[str, object]]) -> dict[str, object]:
    """Write rows to a named table."""
    return {"table": name, "written": len(rows)}


TOOLS = {
    "sql_query": sql_query,
    "transform_rows": transform_rows,
    "validate_schema": validate_schema,
    "write_table": write_table,
}

SYSTEM = (
    "You are a data agent. Use SQL, transform, validate, and write_table tools "
    "to process a dataset. Call tools until the dataset is written."
)


def run(dataset: str, *, model: str = BASELINE_MODEL) -> int:
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=http_timeout()) as client:
            run_tool_agent(
                client,
                base,
                model=model,
                system=SYSTEM,
                user=(
                    f"Load and process dataset {dataset!r}. Query it, transform rows "
                    f"(op=double), validate the schema, and write table {dataset}_out."
                ),
                tools=TOOLS,
            )
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="sales")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    return run(args.input, model=args.model)


if __name__ == "__main__":
    raise SystemExit(main())
