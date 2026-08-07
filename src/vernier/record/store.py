"""Append-only on-disk record store (LF JSONL + blob files)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from vernier.canonical import digest
from vernier.canonical._jcs import serialize_jcs
from vernier.record.schema import RECORD_FORMAT_VERSION, Event, EventKind


class RecordStore:
    """Process-wide append-only store for one run directory."""

    def __init__(self, root: Path, run_id: str) -> None:
        self.root = root
        self.run_id = run_id
        self._lock = threading.Lock()
        self._seq = 0
        self._blob_counter = 0
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "blobs").mkdir(parents=True, exist_ok=True)
        meta_path = self.root / "run.json"
        if not meta_path.exists():
            meta = {
                "format_version": RECORD_FORMAT_VERSION,
                "run_id": run_id,
            }
            meta_path.write_bytes(serialize_jcs(meta) + b"\n")

    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    def next_seq(self) -> int:
        """Assign the next ``seq`` at call initiation (thread-safe)."""
        with self._lock:
            seq = self._seq
            self._seq += 1
            return seq

    def write_blob(self, data: bytes, *, label: str | None = None) -> str:
        """Persist raw bytes; return a ``blob_ref``."""
        with self._lock:
            return self._write_blob_unlocked(data, label=label)

    def append_event(
        self,
        *,
        seq: int,
        kind: EventKind,
        payload: dict[str, Any],
        logical_parent_seq: int | None = None,
        blob_data: bytes | None = None,
    ) -> Event:
        """Canonicalize payload, store blobs, append event line.

        The event blob always contains the JCS payload. Optional ``blob_data``
        (e.g. raw HTTP body) is stored separately and referenced from payload.
        """
        with self._lock:
            stored_payload = dict(payload)
            if blob_data is not None:
                body_ref = self._write_blob_unlocked(blob_data, label="body")
                stored_payload["body_blob_ref"] = body_ref
            payload_bytes = serialize_jcs(stored_payload)
            payload_digest = digest(stored_payload)
            blob_ref = self._write_blob_unlocked(payload_bytes, label="payload")
            event = Event(
                run_id=self.run_id,
                seq=seq,
                logical_parent_seq=logical_parent_seq,
                kind=kind,
                payload_digest=payload_digest,
                blob_ref=blob_ref,
            )
            line = serialize_jcs(
                {
                    "run_id": event.run_id,
                    "seq": event.seq,
                    "logical_parent_seq": event.logical_parent_seq,
                    "kind": event.kind.value,
                    "payload_digest": event.payload_digest,
                    "blob_ref": event.blob_ref,
                }
            )
            with self.events_path.open("ab") as fh:
                fh.write(line + b"\n")
            return event

    def _write_blob_unlocked(self, data: bytes, *, label: str | None = None) -> str:
        blob_id = self._blob_counter
        self._blob_counter += 1
        suffix = f":{label}" if label else ""
        ref = f"blob:{blob_id}{suffix}"
        # Filename uses numeric id only for stable path lookup.
        path = self.root / "blobs" / f"{blob_id}.bin"
        path.write_bytes(data)
        return ref

    def read_events(self) -> list[dict[str, Any]]:
        """Load all event envelopes (test helper)."""
        if not self.events_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.events_path.read_bytes().splitlines():
            if line:
                events.append(json.loads(line.decode("utf-8")))
        return events

    def read_payload(self, event: dict[str, Any]) -> dict[str, Any]:
        """Load the JCS payload object for an event envelope."""
        blob_id = event["blob_ref"].split(":")[1]
        raw = (self.root / "blobs" / f"{blob_id}.bin").read_bytes()
        result: dict[str, Any] = json.loads(raw.decode("utf-8"))
        return result
