#!/usr/bin/env python3
"""Support agent: lookup -> decide -> act (>=8 steps, >=4 tools)."""

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
def lookup_customer(customer_id: str) -> dict[str, str]:
    return {
        "id": customer_id,
        "tier": "gold" if customer_id.endswith("1") else "silver",
    }


@tool
def lookup_ticket(ticket_id: str) -> dict[str, str]:
    return {"id": ticket_id, "issue": "billing", "priority": "high"}


@tool
def decide_action(tier: str, issue: str) -> dict[str, str]:
    action = "refund" if tier == "gold" and issue == "billing" else "escalate"
    return {"action": action}


@tool
def apply_action(customer_id: str, action: str) -> dict[str, str]:
    return {"customer_id": customer_id, "action": action, "status": "applied"}


def run(case: str) -> int:
    # case format: customer:ticket e.g. cust1:t9
    customer_id, ticket_id = (case.split(":", 1) + ["t0"])[:2]
    server, base = start_baseline_server()
    try:
        with httpx.Client(timeout=10.0) as client:
            chat(client, base, f"support case {case}")
            cust = lookup_customer(customer_id)
            ticket = lookup_ticket(ticket_id)
            chat(client, base, f"decide for {customer_id}")
            decision = decide_action(str(cust["tier"]), str(ticket["issue"]))
            chat(client, base, f"action={decision['action']}")
            apply_action(customer_id, str(decision["action"]))
            lookup_ticket(ticket_id)
            chat(client, base, f"confirm support {case}")
            apply_action(customer_id, "notify")
        return 0
    finally:
        if server is not None:
            server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="cust1:t9")
    parser.add_argument("--model", default=BASELINE_MODEL)
    args = parser.parse_args()
    del args.model
    return run(args.input)


if __name__ == "__main__":
    raise SystemExit(main())
