"""Project recorded events into align/classify trajectory symbols."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from vernier.align.types import AlignEvent, AlignKind
from vernier.classify.types import TrajectoryEvent
from vernier.record.schema import EventKind
from vernier.record.store import load_run


def project_run(root: Path) -> list[TrajectoryEvent]:
    """Project a recorded run to trajectory events for align/classify."""
    frozen = load_run(root)
    from vernier.record.store import RecordStore

    reader = RecordStore(frozen.root, frozen.run_id)
    events: list[TrajectoryEvent] = []
    for envelope in reader.read_events():
        kind = envelope["kind"]
        payload = reader.read_payload(envelope)
        projected = _project_one(kind, payload)
        if projected is not None:
            events.append(projected)
    return events


def project_align_events(root: Path) -> list[AlignEvent]:
    traj = project_run(root)
    out: list[AlignEvent] = []
    for ev in traj:
        ak = AlignKind(ev.kind)
        if ak is AlignKind.TOOL_CALL:
            assert ev.tool_name is not None
            out.append(AlignEvent(ak, tool_name=ev.tool_name))
        else:
            out.append(AlignEvent(ak, tool_name=None))
    return out


def _project_one(kind: str, payload: dict[str, Any]) -> TrajectoryEvent | None:
    if kind == EventKind.LLM_REQUEST.value:
        return TrajectoryEvent(kind="LlmTurn")
    if kind == EventKind.TOOL_CALL.value:
        name = payload.get("name")
        if not name:
            return None
        return TrajectoryEvent(
            kind="ToolCall",
            tool_name=str(name),
            arguments={"args_digest": payload.get("args_digest")},
        )
    if kind == EventKind.TOOL_RESULT.value:
        return TrajectoryEvent(kind="ToolResult", is_error=False)
    if kind == EventKind.TOOL_ERROR.value:
        return TrajectoryEvent(kind="ToolResult", is_error=True)
    if kind == EventKind.RUN_END.value:
        return TrajectoryEvent(
            kind="Terminal",
            terminal_kind="FinalMessage",
            final_message="",
        )
    return None
