#!/usr/bin/env python3
"""Booking agent: model-driven tool loop (>=4 tools, >=8 projected steps)."""

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
def search_flights(origin: str, dest: str) -> dict[str, object]:
    """Search flights between origin and destination."""
    return {
        "origin": origin,
        "dest": dest,
        "options": [
            {"id": f"{origin}-{dest}-A", "price": 120},
            {"id": f"{origin}-{dest}-B", "price": 90},
        ],
    }


@tool
def select_option(option_id: str) -> dict[str, str]:
    """Select a flight option id."""
    return {"selected": option_id}


@tool
def hold_seat(option_id: str) -> dict[str, str]:
    """Hold a seat on the selected option."""
    return {"option_id": option_id, "hold": "ok"}


@tool
def confirm_booking(option_id: str, passenger: str) -> dict[str, str]:
    """Confirm a booking for a passenger."""
    return {"option_id": option_id, "passenger": passenger, "status": "confirmed"}


TOOLS = {
    "search_flights": search_flights,
    "select_option": select_option,
    "hold_seat": hold_seat,
    "confirm_booking": confirm_booking,
}

SYSTEM = (
    "You are a booking agent. Search flights, select an option, hold a seat, "
    "and confirm the booking. Use tools until the booking is confirmed."
)


def run(route: str, *, model: str = BASELINE_MODEL) -> int:
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=http_timeout()) as client:
            run_tool_agent(
                client,
                base,
                model=model,
                system=SYSTEM,
                user=(
                    f"Book itinerary {route!r} (format ORIGIN-DEST:passenger). "
                    "Search flights, select an option, hold the seat, and confirm."
                ),
                tools=TOOLS,
            )
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="SFO-JFK:ada")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    return run(args.input, model=args.model)


if __name__ == "__main__":
    raise SystemExit(main())
