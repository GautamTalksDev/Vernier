#!/usr/bin/env python3
"""Booking agent: search -> select -> confirm (>=8 steps, >=4 tools)."""

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
def search_flights(origin: str, dest: str) -> dict[str, object]:
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
    return {"selected": option_id}


@tool
def hold_seat(option_id: str) -> dict[str, str]:
    return {"option_id": option_id, "hold": "ok"}


@tool
def confirm_booking(option_id: str, passenger: str) -> dict[str, str]:
    return {"option_id": option_id, "passenger": passenger, "status": "confirmed"}


def run(route: str) -> int:
    # route: ORIGIN-DEST:passenger e.g. SFO-JFK:ada
    route_part, passenger = (route.split(":", 1) + ["guest"])[:2]
    origin, dest = (route_part.split("-", 1) + ["XXX"])[:2]
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=10.0) as client:
            chat(client, base, f"book {route}")
            found = search_flights(origin, dest)
            chat(client, base, "pick cheapest")
            option = sorted(found["options"], key=lambda o: int(o["price"]))[0]  # type: ignore[index]
            select_option(str(option["id"]))
            hold_seat(str(option["id"]))
            chat(client, base, f"confirm for {passenger}")
            confirm_booking(str(option["id"]), passenger)
            search_flights(origin, dest)
            chat(client, base, f"done booking {route}")
            confirm_booking(str(option["id"]), f"{passenger}-receipt")
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="SFO-JFK:ada")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    del args.model
    return run(args.input)


if __name__ == "__main__":
    raise SystemExit(main())
