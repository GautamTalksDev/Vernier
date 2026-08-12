#!/usr/bin/env python3
"""Data agent: query -> transform -> write (>=8 steps, >=4 tools)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from _corpus_llm import BASELINE_MODEL, chat, http_timeout, start_baseline_server  # noqa: E402

from vern import tool  # noqa: E402


@tool
def sql_query(sql: str) -> dict[str, object]:
    return {"sql": sql, "rows": [{"id": 1, "v": 10}, {"id": 2, "v": 20}]}


@tool
def transform_rows(rows: list[dict[str, object]], op: str) -> list[dict[str, object]]:
    if op == "double":
        return [{"id": r["id"], "v": int(r["v"]) * 2} for r in rows]
    return list(rows)


@tool
def validate_schema(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"ok": all("id" in r and "v" in r for r in rows), "n": len(rows)}


@tool
def write_table(name: str, rows: list[dict[str, object]]) -> dict[str, object]:
    return {"table": name, "written": len(rows)}


def run(dataset: str) -> int:
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=http_timeout()) as client:
            chat(client, base, f"load dataset {dataset}")
            raw = sql_query(f"SELECT * FROM {dataset}")
            chat(client, base, f"transform plan for {dataset}")
            rows = transform_rows(list(raw["rows"]), "double")  # type: ignore[arg-type]
            check = validate_schema(rows)
            chat(
                client,
                base,
                f"validation=ok:{check['ok']}:n:{check['n']}",
            )
            write_table(f"{dataset}_out", rows)
            sql_query(f"SELECT COUNT(*) FROM {dataset}_out")
            chat(client, base, f"finalize write for {dataset}")
            pass  # burnin_control C4: dropped write_table
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="sales")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    del args.model
    return run(args.input)


if __name__ == "__main__":
    raise SystemExit(main())
