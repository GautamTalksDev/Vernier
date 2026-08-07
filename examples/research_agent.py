#!/usr/bin/env python3
"""Research agent: search -> read -> summarize (>=8 steps, >=4 tools)."""

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
def web_search(query: str) -> dict[str, object]:
    return {"query": query, "hits": [f"doc:{query}:1", f"doc:{query}:2"]}


@tool
def fetch_url(url: str) -> dict[str, str]:
    return {"url": url, "body": f"content-of-{url}"}


@tool
def extract_quotes(text: str, limit: int = 2) -> list[str]:
    words = text.split("-")
    return [f"quote:{w}" for w in words[:limit]]


@tool
def write_summary(title: str, bullets: list[str]) -> dict[str, object]:
    return {"title": title, "bullets": bullets, "status": "written"}


def run(topic: str) -> int:
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=10.0) as client:
            chat(client, base, f"research topic: {topic}")
            hits = web_search(topic)
            chat(client, base, f"choose sources for {topic}")
            url = str(hits["hits"][0])
            page = fetch_url(url)
            quotes = extract_quotes(str(page["body"]), limit=2)
            chat(client, base, f"draft summary for {topic}")
            write_summary(topic, quotes)
            fetch_url(str(hits["hits"][1]))
            chat(client, base, f"finalize research on {topic}")
            write_summary(f"{topic}-final", quotes + ["done"])
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="climate")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    del args.model  # baseline wired in chat(); flag kept for corpus CLI symmetry
    return run(args.input)


if __name__ == "__main__":
    raise SystemExit(main())
