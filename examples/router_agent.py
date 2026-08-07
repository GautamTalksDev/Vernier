#!/usr/bin/env python3
"""Multi-tool router agent: classify intent then dispatch (>=8 steps, >=4 tools)."""

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
def classify_intent(text: str) -> dict[str, str]:
    if "weather" in text:
        return {"intent": "weather"}
    if "math" in text:
        return {"intent": "math"}
    return {"intent": "search"}


@tool
def tool_weather(city: str) -> dict[str, str]:
    return {"city": city, "forecast": "clear"}


@tool
def tool_math(expr: str) -> dict[str, object]:
    if expr.isdigit():
        return {"expr": expr, "value": int(expr) * 2}
    return {"expr": expr, "value": len(expr)}


@tool
def tool_search(query: str) -> dict[str, object]:
    return {"query": query, "hits": [f"hit:{query}"]}


@tool
def route_log(intent: str, result: object) -> dict[str, object]:
    return {"intent": intent, "logged": True, "result": result}


def run(utterance: str) -> int:
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=10.0) as client:
            chat(client, base, f"route: {utterance}")
            intent = classify_intent(utterance)["intent"]
            chat(client, base, f"intent={intent}")
            if intent == "weather":
                city = utterance.split()[-1]
                result = tool_weather(city)
            elif intent == "math":
                result = tool_math(utterance.split()[-1])
            else:
                result = tool_search(utterance)
            route_log(intent, result)
            chat(client, base, "secondary check")
            classify_intent(utterance)
            tool_search(f"related:{utterance}")
            chat(client, base, f"complete route {utterance}")
            route_log(intent, {"done": True})
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="weather Paris")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    del args.model
    return run(args.input)


if __name__ == "__main__":
    raise SystemExit(main())
