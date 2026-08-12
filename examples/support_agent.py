#!/usr/bin/env python3
"""Support agent: model-driven tool loop (>=4 tools, >=8 projected steps)."""

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
def lookup_customer(customer_id: str) -> dict[str, str]:
    """Look up a customer record by id."""
    return {
        "id": customer_id,
        "tier": "gold" if customer_id.endswith("1") else "silver",
    }


@tool
def lookup_ticket(ticket_id: str) -> dict[str, str]:
    """Look up a support ticket by id."""
    return {"id": ticket_id, "issue": "billing", "priority": "high"}


@tool
def decide_action(tier: str, issue: str) -> dict[str, str]:
    """Decide a support action from tier and issue."""
    action = "refund" if tier == "gold" and issue == "billing" else "escalate"
    return {"action": action}


@tool
def apply_action(customer_id: str, action: str) -> dict[str, str]:
    """Apply a support action for a customer."""
    return {"customer_id": customer_id, "action": action, "status": "applied"}


TOOLS = {
    "lookup_customer": lookup_customer,
    "lookup_ticket": lookup_ticket,
    "decide_action": decide_action,
    "apply_action": apply_action,
}

SYSTEM = (
    "You are a support agent. Look up the customer and ticket, decide an action, "
    "and apply it. Use the tools until the case is resolved."
)


def run(case: str, *, model: str = BASELINE_MODEL) -> int:
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=http_timeout()) as client:
            run_tool_agent(
                client,
                base,
                model=model,
                system=SYSTEM,
                user=(
                    f"Handle support case {case!r} (format customer:ticket). "
                    "Look up both sides, decide an action, and apply it."
                ),
                tools=TOOLS,
            )
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="cust1:t9")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    return run(args.input, model=args.model)


if __name__ == "__main__":
    raise SystemExit(main())
