"""Tests for vernier-record-v1 schema types."""

from __future__ import annotations

import pytest

from vernier.record import RECORD_FORMAT_VERSION, Event, EventKind, Run


def _event(
    seq: int,
    kind: EventKind = EventKind.TOOL_CALL,
    *,
    parent: int | None = None,
    run_id: str = "run-1",
) -> Event:
    return Event(
        run_id=run_id,
        seq=seq,
        logical_parent_seq=parent,
        kind=kind,
        payload_digest="sha256:" + ("ab" * 32),
        blob_ref=f"blob:{seq}",
    )


def test_record_format_version_constant() -> None:
    assert RECORD_FORMAT_VERSION == "vernier-record-v1"


def test_all_event_kinds_present() -> None:
    names = {k.value for k in EventKind}
    assert names == {
        "RunStart",
        "LlmRequest",
        "LlmResponse",
        "ToolCall",
        "ToolResult",
        "ToolError",
        "EnvRead",
        "WallClock",
        "Random",
        "ReplayMiss",
        "RunEnd",
        "CaptureGap",
    }


def test_run_accepts_ordered_events() -> None:
    events = (
        _event(0, EventKind.RUN_START),
        _event(1, EventKind.LLM_REQUEST, parent=0),
        _event(2, EventKind.TOOL_CALL, parent=1),
        _event(3, EventKind.TOOL_RESULT, parent=2),
        _event(4, EventKind.RUN_END, parent=0),
    )
    run = Run(format_version="vernier-record-v1", run_id="run-1", events=events)
    assert len(run.events) == 5
    assert run.events[2].seq == 2


def test_run_rejects_duplicate_seq() -> None:
    with pytest.raises(ValueError, match="duplicate seq"):
        Run(
            format_version="vernier-record-v1",
            run_id="run-1",
            events=(_event(0), _event(0)),
        )


def test_event_requires_sha256_digest() -> None:
    with pytest.raises(ValueError, match="sha256:"):
        Event(
            run_id="run-1",
            seq=0,
            logical_parent_seq=None,
            kind=EventKind.RUN_START,
            payload_digest="md5:dead",
            blob_ref="blob:0",
        )


def test_event_rejects_negative_seq_and_empty_blob() -> None:
    with pytest.raises(ValueError, match="seq must be"):
        Event(
            run_id="run-1",
            seq=-1,
            logical_parent_seq=None,
            kind=EventKind.RUN_START,
            payload_digest="sha256:" + ("ab" * 32),
            blob_ref="blob:0",
        )
    with pytest.raises(ValueError, match="logical_parent_seq"):
        Event(
            run_id="run-1",
            seq=0,
            logical_parent_seq=-1,
            kind=EventKind.RUN_START,
            payload_digest="sha256:" + ("ab" * 32),
            blob_ref="blob:0",
        )
    with pytest.raises(ValueError, match="blob_ref"):
        Event(
            run_id="run-1",
            seq=0,
            logical_parent_seq=None,
            kind=EventKind.RUN_START,
            payload_digest="sha256:" + ("ab" * 32),
            blob_ref="",
        )


def test_run_rejects_bad_version_and_mismatched_ids() -> None:
    with pytest.raises(ValueError, match="unsupported record format"):
        Run(
            format_version="vernier-record-v0",  # type: ignore[arg-type]
            run_id="run-1",
            events=(),
        )
    with pytest.raises(ValueError, match="run_id must be non-empty"):
        Run(format_version="vernier-record-v1", run_id="", events=())
    with pytest.raises(ValueError, match="run_id mismatch"):
        Run(
            format_version="vernier-record-v1",
            run_id="run-1",
            events=(_event(0, run_id="other"),),
        )
