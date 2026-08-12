#!/usr/bin/env python3
"""Research agent: model-driven tool loop (>=4 tools, >=8 projected steps)."""

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
def web_search(query: str) -> dict[str, object]:
    """Search the web for documents matching query."""
    return {"query": query, "hits": [f"doc:{query}:1", f"doc:{query}:2"]}


@tool
def fetch_url(url: str) -> dict[str, str]:
    """Fetch the body of a document URL."""
    return {"url": url, "body": f"content-of-{url}"}


@tool
def extract_quotes(text: str, limit: int = 2) -> list[str]:
    """Extract short quotes from text."""
    words = text.replace(":", "-").split("-")
    return [f"quote:{w}" for w in words[:limit] if w]


@tool
def write_summary(title: str, bullets: list[str]) -> dict[str, object]:
    """Write a titled summary from bullet points."""
    return {"title": title, "bullets": bullets, "status": "written"}


TOOLS = {
    "web_search": web_search,
    "fetch_url": fetch_url,
    "extract_quotes": extract_quotes,
    "write_summary": write_summary,
}

SYSTEM = (
    "You are a research agent. Use the available tools to search, fetch sources, "
    "extract quotes, and write a summary. Call tools as needed until the task is done."
)


def run(topic: str, *, model: str = BASELINE_MODEL) -> int:
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=http_timeout()) as client:
            run_tool_agent(
                client,
                base,
                model=model,
                system=SYSTEM,
                user=(
                    f"Research the topic {topic!r}. Search for sources, fetch at "
                    f"least one document, extract quotes, and write a summary "
                    f"titled {topic!r}."
                ),
                tools=TOOLS,
            )
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="climate")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    return run(args.input, model=args.model)


if __name__ == "__main__":
    raise SystemExit(main())
