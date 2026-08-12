"""Record store: CRC lines, content-addressed blobs, verify, dedupe."""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from vernier.canonical import digest
from vernier.canonical._jcs import serialize_jcs
from vernier.record.index import list_runs, upsert_run
from vernier.record.schema import Event, EventKind
from vernier.record.store import (
    FrozenRun,
    RecordStore,
    load_run,
    verify_run,
)


def test_max_events_root_digest_and_corruption(tmp_path: Path) -> None:
    from vernier.record.store import MAX_EVENTS

    run_dir = tmp_path / "runs" / "big"
    index = tmp_path / "index.sqlite"
    store = RecordStore(run_dir, "big", index_path=index)
    for i in range(MAX_EVENTS):
        store.append_event(
            seq=store.next_seq(),
            kind=EventKind.LLM_REQUEST,
            payload={"i": i, "msg": "hello"},
        )
    manifest = store.finalize()
    assert manifest.event_count == MAX_EVENTS

    result = verify_run(run_dir)
    assert result.ok, result.errors

    frozen = load_run(run_dir)
    assert isinstance(frozen, FrozenRun)
    assert len(frozen.events) == MAX_EVENTS
    assert frozen.manifest.root_digest == manifest.root_digest

    events_path = run_dir / "events.jsonl"
    data = bytearray(events_path.read_bytes())
    pos = max(20, len(data) // 2)
    data[pos] = (data[pos] + 1) % 256
    events_path.write_bytes(data)
    corrupted = verify_run(run_dir)
    assert not corrupted.ok
    assert corrupted.errors


def test_blob_corruption_detected(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "blobrun"
    store = RecordStore(run_dir, "blobrun")
    store.append_event(
        seq=store.next_seq(),
        kind=EventKind.RUN_START,
        payload={"run_id": "blobrun"},
        blob_data=b"payload-bytes-for-blob",
    )
    store.finalize()
    assert verify_run(run_dir).ok

    target = next((run_dir / "blobs").iterdir())
    raw = bytearray(target.read_bytes())
    raw[-1] = (raw[-1] + 1) % 256
    target.write_bytes(raw)

    result = verify_run(run_dir)
    assert not result.ok
    assert any("mismatch" in e or "gzip" in e for e in result.errors)


def test_dedup_ratio_repeated_system_prompt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / "runs" / "dedupe"
    store = RecordStore(run_dir, "dedupe")
    prompt = b"S" * (20 * 1024)
    turns = 50
    for i in range(turns):
        store.append_event(
            seq=store.next_seq(),
            kind=EventKind.LLM_REQUEST,
            payload={"turn": i, "role": "system"},
            blob_data=prompt,
        )
    store.finalize()
    stats = store.dedup_stats
    assert stats.logical_bytes >= turns * len(prompt)
    prompt_digest = hashlib.sha256(prompt).hexdigest()
    assert (run_dir / "blobs" / f"{prompt_digest}.gz").exists()
    stored_prompt = len(gzip.compress(prompt, mtime=0))
    ratio = stats.ratio
    print(
        f"DEDUP_RATIO logical_bytes={stats.logical_bytes} "
        f"stored_bytes={stats.stored_bytes} ratio={ratio:.2f} "
        f"prompt_stored={stored_prompt} turns={turns}"
    )
    captured = capsys.readouterr()
    assert "DEDUP_RATIO" in captured.out
    assert stats.logical_bytes / stored_prompt >= 10
    assert ratio > 5
    assert verify_run(run_dir).ok


def test_no_method_can_mutate_existing_event(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "run", "immutable")
    store.append_event(
        seq=0,
        kind=EventKind.RUN_START,
        payload={"run_id": "immutable"},
    )
    store.finalize()

    for name in ("update", "delete", "remove", "overwrite", "replace"):
        assert not hasattr(RecordStore, name)
        with pytest.raises(AttributeError):
            getattr(store, name)()  # type: ignore[misc]

    frozen = load_run(tmp_path / "run")
    with pytest.raises(FrozenInstanceError):
        frozen.events = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        frozen.run_id = "mutated"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        frozen.events[0].seq = 99  # type: ignore[misc]


def test_append_only_rejects_after_finalize(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "run", "closed")
    pending = Event(
        run_id="closed",
        seq=1,
        logical_parent_seq=None,
        kind=EventKind.RUN_END,
        payload_digest=digest({"x": 2}),
        blob_ref=store.put_blob(serialize_jcs({"x": 2})),
    )
    store.append_event(seq=0, kind=EventKind.RUN_START, payload={"x": 1})
    store.finalize()
    with pytest.raises(RuntimeError, match="finalized"):
        store.append_event(seq=1, kind=EventKind.RUN_END, payload={"x": 2})
    with pytest.raises(RuntimeError, match="finalized"):
        store.append(pending)
    with pytest.raises(RuntimeError, match="finalized"):
        store.put_blob(b"nope")


def test_sqlite_index_not_source_of_truth(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    index = tmp_path / "index.sqlite"
    store = RecordStore(runs / "r1", "r1", index_path=index)
    store.append_event(seq=0, kind=EventKind.RUN_START, payload={"run_id": "r1"})
    manifest = store.finalize()
    rows = list_runs(index)
    assert len(rows) == 1
    assert rows[0].root_digest == manifest.root_digest
    upsert_run(
        index,
        run_id="r1",
        path=str(runs / "r1"),
        created_at="tampered",
        event_count=999,
        root_digest="sha256:" + ("00" * 32),
    )
    assert verify_run(runs / "r1").ok


def test_direct_append(tmp_path: Path) -> None:
    store = RecordStore(tmp_path / "run", "e1")
    payload = {"ok": True}
    blob_ref = store.put_blob(serialize_jcs(payload))
    store.append(
        Event(
            run_id="e1",
            seq=0,
            logical_parent_seq=None,
            kind=EventKind.RUN_START,
            payload_digest=digest(payload),
            blob_ref=blob_ref,
        )
    )
    store.finalize()
    assert verify_run(tmp_path / "run").ok
