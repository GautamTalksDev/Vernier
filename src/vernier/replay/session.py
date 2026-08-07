"""Replay session state (catalog + recording store for the re-drive)."""

from __future__ import annotations

import os
from pathlib import Path

from vernier.record.store import RecordStore
from vernier.replay.catalog import ReplayCatalog, load_catalog
from vernier.replay.miss import ReplayMiss

_ENV_REPLAY = "VERNIER_REPLAY"
_ENV_SOURCE = "VERNIER_REPLAY_SOURCE"

_catalog: ReplayCatalog | None = None


def is_replay() -> bool:
    return _catalog is not None


def get_catalog() -> ReplayCatalog | None:
    return _catalog


def activate_replay_from_env() -> RecordStore | None:
    """Activate recording into VERNIER_RUN_DIR while stubbing from source run."""
    global _catalog
    from vernier.capture.recorder import activate_store, safe_append
    from vernier.record.schema import EventKind

    if os.environ.get(_ENV_REPLAY) != "1":
        return None
    run_dir = os.environ.get("VERNIER_RUN_DIR")
    run_id = os.environ.get("VERNIER_RUN_ID")
    source = os.environ.get(_ENV_SOURCE)
    if not run_dir or not run_id or not source:
        return None

    catalog = load_catalog(Path(source))
    _catalog = catalog

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
    safe_append(
        store,
        seq=store.next_seq(),
        kind=EventKind.RUN_START,
        payload={
            "run_id": run_id,
            "command": list(catalog.command),
            "cwd": catalog.cwd,
            "replay_of": catalog.source_run_id,
        },
    )
    return store


def deactivate_replay() -> None:
    global _catalog
    _catalog = None


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
