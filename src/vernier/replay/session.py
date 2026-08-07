"""Replay / live-diff session state."""

from __future__ import annotations

import os
from pathlib import Path

from vernier.record.store import RecordStore
from vernier.replay.catalog import ReplayCatalog, load_catalog
from vernier.replay.miss import ReplayMiss, ReplayToolMiss

_ENV_REPLAY = "VERNIER_REPLAY"
_ENV_DIFF = "VERNIER_DIFF"
_ENV_SOURCE = "VERNIER_REPLAY_SOURCE"

_catalog: ReplayCatalog | None = None
_live_diff: bool = False


def is_replay() -> bool:
    """True when a catalog is loaded (full stub or live-diff)."""
    return _catalog is not None


def is_live_diff() -> bool:
    """True when LLM traffic is live and only tools are stubbed."""
    return _live_diff and _catalog is not None


def stub_http() -> bool:
    """Whether HTTP/LLM calls should be served from the record."""
    return _catalog is not None and not _live_diff


def get_catalog() -> ReplayCatalog | None:
    return _catalog


def activate_replay_from_env() -> RecordStore | None:
    """Activate full-stub (``VERNIER_REPLAY=1``) or live-diff (``VERNIER_DIFF=1``)."""
    global _catalog, _live_diff
    from vernier.capture.recorder import activate_store, safe_append
    from vernier.record.schema import EventKind

    live = os.environ.get(_ENV_DIFF) == "1"
    full = os.environ.get(_ENV_REPLAY) == "1"
    if not live and not full:
        return None

    run_dir = os.environ.get("VERNIER_RUN_DIR")
    run_id = os.environ.get("VERNIER_RUN_ID")
    source = os.environ.get(_ENV_SOURCE)
    if not run_dir or not run_id or not source:
        return None

    catalog = load_catalog(Path(source))
    _catalog = catalog
    _live_diff = live

    index_env = os.environ.get("VERNIER_INDEX")
    index_path = Path(index_env) if index_env else None
    store = RecordStore(
        Path(run_dir),
        run_id,
        index_path=index_path,
        command=list(catalog.command),
        cwd=catalog.cwd,
    )
    activate_store(store)
    mode = "live_diff" if live else "full_stub"
    safe_append(
        store,
        seq=store.next_seq(),
        kind=EventKind.RUN_START,
        payload={
            "run_id": run_id,
            "command": list(catalog.command),
            "cwd": catalog.cwd,
            "replay_of": catalog.source_run_id,
            "mode": mode,
        },
    )
    return store


def deactivate_replay() -> None:
    global _catalog, _live_diff
    _catalog = None
    _live_diff = False


def record_replay_miss(
    *,
    channel: str,
    detail: str,
    logical_parent_seq: int | None = None,
) -> ReplayMiss:
    """Append a ReplayMiss event (if recording) and return the exception."""
    from vernier.capture.recorder import get_store, safe_append
    from vernier.record.schema import EventKind

    store = get_store()
    if store is not None:
        safe_append(
            store,
            seq=store.next_seq(),
            kind=EventKind.REPLAY_MISS,
            payload={"channel": channel, "detail": detail},
            logical_parent_seq=logical_parent_seq,
        )
    return ReplayMiss(channel, detail)


def tool_miss_as_error(
    *,
    name: str,
    detail: str,
    logical_parent_seq: int | None = None,
) -> ReplayToolMiss:
    """Record ReplayMiss + ToolError and return a typed tool error for the agent."""
    from vernier.capture.recorder import get_store, safe_append
    from vernier.record.schema import EventKind

    store = get_store()
    miss = record_replay_miss(
        channel="tool",
        detail=detail,
        logical_parent_seq=logical_parent_seq,
    )
    if store is not None:
        safe_append(
            store,
            seq=store.next_seq(),
            kind=EventKind.TOOL_ERROR,
            payload={
                "channel": "function",
                "name": name,
                "error_type": "ReplayToolMiss",
                "error": str(miss),
            },
            logical_parent_seq=logical_parent_seq,
        )
    return ReplayToolMiss(name=name, detail=detail)
