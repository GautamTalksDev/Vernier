#!/usr/bin/env python3
"""Router agent: model-driven tool loop (>=4 tools, >=8 projected steps)."""

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
def classify_intent(text: str) -> dict[str, str]:
    """Classify user intent as weather, math, or search."""
    if "weather" in text:
        return {"intent": "weather"}
    if "math" in text:
        return {"intent": "math"}
    return {"intent": "search"}


@tool
def tool_weather(city: str) -> dict[str, str]:
    """Get a weather forecast for a city."""
    return {"city": city, "forecast": "clear"}


@tool
def tool_math(expr: str) -> dict[str, object]:
    """Evaluate a simple math expression."""
    if expr.isdigit():
        return {"expr": expr, "value": int(expr) * 2}
    return {"expr": expr, "value": len(expr)}


@tool
def tool_search(query: str) -> dict[str, object]:
    """Search for a query string."""
    return {"query": query, "hits": [f"hit:{query}"]}


@tool
def route_log(intent: str, result: object) -> dict[str, object]:
    """Log a routing decision and result."""
    return {"intent": intent, "logged": True, "result": result}


TOOLS = {
    "classify_intent": classify_intent,
    "tool_weather": tool_weather,
    "tool_math": tool_math,
    "tool_search": tool_search,
    "route_log": route_log,
}

SYSTEM = (
    "You are a multi-tool router. Classify the user's intent, call the matching "
    "domain tool (weather/math/search), and log the result. Decide which tools "
    "to call — do not skip classification."
)


def run(utterance: str, *, model: str = BASELINE_MODEL) -> int:
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=http_timeout()) as client:
            run_tool_agent(
                client,
                base,
                model=model,
                system=SYSTEM,
                user=(
                    f"Route this request: {utterance!r}. Classify intent, invoke the "
                    "appropriate tool, and log the outcome."
                ),
                tools=TOOLS,
            )
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="weather Paris")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    return run(args.input, model=args.model)


if __name__ == "__main__":
    raise SystemExit(main())
