"""Live-diff request parity: control fields must match the baseline recording."""

from __future__ import annotations

import json
import os
from typing import Any


class HarnessRequestBodyMismatch(RuntimeError):
    """Candidate LLM request body differs from baseline outside allowed overrides."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def request_options_from_body(body: bytes) -> str:
    """Canonical JSON for ``options`` (or ``null``) for logging."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "(unparseable)"
    if not isinstance(data, dict):
        return "(not-object)"
    opts = data.get("options")
    if opts is None:
        return "null"
    return json.dumps(opts, sort_keys=True, separators=(",", ":"))


def check_live_diff_request_parity(
    candidate_body: bytes,
    baseline_body: bytes,
    *,
    turn_index: int,
) -> list[str]:
    """Return human-readable mismatch descriptions (empty when parity holds).

    Allowed diffs:
      - ``/model`` when ``VERNIER_OVERRIDE_MODEL`` is set and candidate matches it
      - ``/seed`` when ``VERNIER_LLM_SEED`` is set and candidate matches it
      - ``/messages`` only after turn 0 (model-driven history drift)

    Control envelope (``temperature``, ``stream``, ``options``, ``tools``,
    ``tool_choice``) must always match. ``/messages`` must match at turn 0.
    """
    cand = _parse_obj(candidate_body)
    base = _parse_obj(baseline_body)
    if cand is None or base is None:
        return ["body is not valid JSON object"]

    mismatches: list[str] = []
    override_model = (os.environ.get("VERNIER_OVERRIDE_MODEL") or "").strip()
    override_seed = (os.environ.get("VERNIER_LLM_SEED") or "").strip()

    for field in ("temperature", "stream", "tool_choice"):
        if cand.get(field) != base.get(field):
            mismatches.append(
                f"/{field}: {base.get(field)!r} -> {cand.get(field)!r}"
            )

    if cand.get("options") != base.get("options"):
        mismatches.append(
            f"/options: {base.get('options')!r} -> {cand.get('options')!r}"
        )

    if cand.get("tools") != base.get("tools"):
        mismatches.append("/tools: schema differs from baseline")

    if cand.get("model") != base.get("model"):
        if not override_model or cand.get("model") != override_model:
            mismatches.append(
                f"/model: {base.get('model')!r} -> {cand.get('model')!r}"
            )

    if cand.get("seed") != base.get("seed"):
        if not override_seed or str(cand.get("seed")) != override_seed:
            mismatches.append(
                f"/seed: {base.get('seed')!r} -> {cand.get('seed')!r}"
            )

    if turn_index == 0 and cand.get("messages") != base.get("messages"):
        mismatches.append("/messages: turn-0 history differs from baseline")

    return mismatches


def _parse_obj(body: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
