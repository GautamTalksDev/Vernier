"""Tests for live-diff request body parity checks."""

from __future__ import annotations

import json

import pytest

from vernier.replay.request_parity import check_live_diff_request_parity


def _body(**fields: object) -> bytes:
    base = {
        "model": "qwen3:4b-instruct",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.0,
        "seed": 42,
        "stream": False,
        "tools": [],
        "tool_choice": "auto",
    }
    base.update(fields)
    return json.dumps(base, separators=(",", ":")).encode()


def test_turn_zero_messages_must_match() -> None:
    base = _body()
    cand = _body(messages=[{"role": "user", "content": "bye"}])
    mismatches = check_live_diff_request_parity(cand, base, turn_index=0)
    assert any("/messages" in m for m in mismatches)


def test_options_num_ctx_mismatch_fails() -> None:
    base = _body()
    cand = _body(options={"num_ctx": 16384})
    mismatches = check_live_diff_request_parity(cand, base, turn_index=0)
    assert any("/options" in m for m in mismatches)


def test_seed_override_allowed_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VERNIER_LLM_SEED", "43")
    base = _body(seed=42)
    cand = _body(seed=43)
    assert check_live_diff_request_parity(cand, base, turn_index=0) == []


def test_model_override_allowed_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VERNIER_OVERRIDE_MODEL", "llama3.2:3b")
    base = _body(model="qwen3:4b-instruct")
    cand = _body(model="llama3.2:3b")
    assert check_live_diff_request_parity(cand, base, turn_index=0) == []


def test_messages_may_differ_after_turn_zero() -> None:
    base = _body(messages=[{"role": "user", "content": "a"}])
    cand = _body(messages=[{"role": "user", "content": "b"}])
    assert check_live_diff_request_parity(cand, base, turn_index=1) == []
