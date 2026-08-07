"""Request body overrides for live-diff (model / system prompt)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def apply_live_overrides(body: bytes) -> bytes:
    """Rewrite JSON chat-completions bodies when override env vars are set.

    - ``VERNIER_OVERRIDE_MODEL`` replaces ``model``
    - ``VERNIER_OVERRIDE_PROMPT_FILE`` replaces/inserts the system message
    """
    model = os.environ.get("VERNIER_OVERRIDE_MODEL")
    prompt_file = os.environ.get("VERNIER_OVERRIDE_PROMPT_FILE")
    if not model and not prompt_file:
        return body
    if not body:
        return body
    try:
        text = body.decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    if not isinstance(data, dict):
        return body
    changed = False
    if model:
        data["model"] = model
        changed = True
    if prompt_file:
        prompt = Path(prompt_file).read_text(encoding="utf-8")
        _set_system_prompt(data, prompt)
        changed = True
    if not changed:
        return body
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _set_system_prompt(data: dict[str, Any], prompt: str) -> None:
    messages = data.get("messages")
    if not isinstance(messages, list):
        data["messages"] = [{"role": "system", "content": prompt}]
        return
    for item in messages:
        if isinstance(item, dict) and item.get("role") == "system":
            item["content"] = prompt
            return
    messages.insert(0, {"role": "system", "content": prompt})
