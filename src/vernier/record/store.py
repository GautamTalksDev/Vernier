"""Append-only, content-addressed record store.

Layout under ``./.vernier/runs/<run_id>/``::

    events.jsonl   CRC32-prefixed canonical event lines (source of truth)
    blobs/<sha256> content-addressed payloads (``.gz`` when > 4 KiB)
    manifest.json  run metadata + root digest over the event sequence

There is no update and no delete. Loading returns a frozen immutable run.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from vernier.canonical import CANON_VERSION_TAG, digest
from vernier.canonical._jcs import serialize_jcs
from vernier.record.schema import RECORD_FORMAT_VERSION, Event, EventKind

GZIP_THRESHOLD: Final = 4 * 1024
CANON_VERSION: Final = "vernier-canon-v1"
DEFAULT_RUNS_DIR: Final = Path(".vernier/runs")


@dataclass(frozen=True, slots=True)
class DedupStats:
    """Logical vs on-disk blob bytes for a store session."""

    logical_bytes: int
    stored_bytes: int

    @property
    def ratio(self) -> float:
        if self.stored_bytes == 0:
            return 1.0
        return self.logical_bytes / self.stored_bytes


@dataclass(frozen=True, slots=True)
class Manifest:
    run_id: str
    created_at: str
    canon_version: str
    record_version: str
    event_count: int
    root_digest: str


@dataclass(frozen=True, slots=True)
class FrozenRun:
    """Immutable loaded run (JSONL is the source of truth)."""

    run_id: str
    root: Path
    manifest: Manifest
    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    run_id: str
    errors: tuple[str, ...]


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _crc32_hex(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"


def encode_event_line(body: bytes) -> bytes:
    """Prefix a canonical event body with its CRC32 (8 hex chars + space)."""
    return f"{_crc32_hex(body)} ".encode("ascii") + body + b"\n"


def decode_event_line(line: bytes) -> bytes:
    """Validate CRC prefix and return the line body (without newline)."""
    raw = line[:-1] if line.endswith(b"\n") else line
    if len(raw) < 10 or raw[8:9] != b" ":
        raise ValueError("event line missing CRC32 prefix")
    prefix, body = raw[:8], raw[9:]
    actual = _crc32_hex(body)
    if prefix.decode("ascii") != actual:
        raise ValueError(
            f"CRC32 mismatch: declared={prefix.decode('ascii')} actual={actual}"
        )
    return body


def root_digest_for_bodies(bodies: list[bytes]) -> str:
    """Root digest over ordered event line bodies (length-prefixed)."""
    hasher = hashlib.sha256()
    hasher.update(CANON_VERSION_TAG)
    hasher.update(b"vernier-record-root-v1\n")
    for body in bodies:
        hasher.update(len(body).to_bytes(8, "big"))
        hasher.update(body)
    return f"sha256:{hasher.hexdigest()}"


def blob_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class RecordStore:
    """Append-only writer for one run directory.

    Public write surface: :meth:`next_seq`, :meth:`put_blob`, :meth:`append`,
    :meth:`append_event`, :meth:`finalize`. No update/delete.
    """

    def __init__(
        self,
        root: Path,
        run_id: str,
        *,
        created_at: str | None = None,
        index_path: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.run_id = run_id
        self._lock = threading.Lock()
        self._seq = 0
        self._event_count = 0
        self._event_bodies: list[bytes] = []
        self._logical_bytes = 0
        self._stored_bytes = 0
        self._created_at = created_at or _utc_now_iso()
        self._index_path = index_path
        self._finalized = False

        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        if not self.events_path.exists():
            self.events_path.write_bytes(b"")

    @property
    def blobs_dir(self) -> Path:
        return self.root / "blobs"

    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def dedup_stats(self) -> DedupStats:
        return DedupStats(
            logical_bytes=self._logical_bytes,
            stored_bytes=self._stored_bytes,
        )

    def next_seq(self) -> int:
        """Assign the next ``seq`` at call initiation (thread-safe)."""
        with self._lock:
            seq = self._seq
            self._seq += 1
            return seq

    def put_blob(self, data: bytes) -> str:
        """Store bytes content-addressed; return ``sha256:<hex>``."""
        with self._lock:
            if self._finalized:
                raise RuntimeError("store is finalized; cannot put_blob")
            return self._put_blob_unlocked(data)

    def write_blob(self, data: bytes, *, label: str | None = None) -> str:
        """Alias for :meth:`put_blob` (label ignored; addressing is by digest)."""
        del label
        return self.put_blob(data)

    def append(self, event: Event) -> None:
        """Append one event envelope. The only event-mutation API."""
        if event.run_id != self.run_id:
            raise ValueError(
                f"event run_id {event.run_id!r} != store run_id {self.run_id!r}"
            )
        body = serialize_jcs(
            {
                "run_id": event.run_id,
                "seq": event.seq,
                "logical_parent_seq": event.logical_parent_seq,
                "kind": event.kind.value,
                "payload_digest": event.payload_digest,
                "blob_ref": event.blob_ref,
            }
        )
        with self._lock:
            if self._finalized:
                raise RuntimeError("store is finalized; cannot append")
            self._append_body_unlocked(body)

    def append_event(
        self,
        *,
        seq: int,
        kind: EventKind,
        payload: dict[str, Any],
        logical_parent_seq: int | None = None,
        blob_data: bytes | None = None,
    ) -> Event:
        """Build payload/body blobs, then :meth:`append` the event."""
        with self._lock:
            if self._finalized:
                raise RuntimeError("store is finalized; cannot append")
            stored_payload = dict(payload)
            if blob_data is not None:
                stored_payload["body_blob_ref"] = self._put_blob_unlocked(blob_data)
            payload_bytes = serialize_jcs(stored_payload)
            payload_digest = digest(stored_payload)
            blob_ref = self._put_blob_unlocked(payload_bytes)
            event = Event(
                run_id=self.run_id,
                seq=seq,
                logical_parent_seq=logical_parent_seq,
                kind=kind,
                payload_digest=payload_digest,
                blob_ref=blob_ref,
            )
            body = serialize_jcs(
                {
                    "run_id": event.run_id,
                    "seq": event.seq,
                    "logical_parent_seq": event.logical_parent_seq,
                    "kind": event.kind.value,
                    "payload_digest": event.payload_digest,
                    "blob_ref": event.blob_ref,
                }
            )
            self._append_body_unlocked(body)
            return event

    def finalize(self) -> Manifest:
        """Write manifest and refresh the SQLite index (idempotent)."""
        with self._lock:
            manifest = Manifest(
                run_id=self.run_id,
                created_at=self._created_at,
                canon_version=CANON_VERSION,
                record_version=RECORD_FORMAT_VERSION,
                event_count=self._event_count,
                root_digest=root_digest_for_bodies(list(self._event_bodies)),
            )
            payload = {
                "run_id": manifest.run_id,
                "created_at": manifest.created_at,
                "canon_version": manifest.canon_version,
                "record_version": manifest.record_version,
                "event_count": manifest.event_count,
                "root_digest": manifest.root_digest,
            }
            self.manifest_path.write_bytes(serialize_jcs(payload) + b"\n")
            self._finalized = True
            if self._index_path is not None:
                from vernier.record.index import upsert_run

                upsert_run(
                    self._index_path,
                    run_id=self.run_id,
                    path=str(self.root.resolve()),
                    created_at=manifest.created_at,
                    event_count=manifest.event_count,
                    root_digest=manifest.root_digest,
                )
            return manifest

    def read_blob(self, ref: str) -> bytes:
        """Load a content-addressed blob by ``sha256:<hex>`` ref."""
        return _read_blob_file(self.blobs_dir, ref)

    def read_events(self) -> list[dict[str, Any]]:
        """Load event envelopes from JSONL (test/helper; verifies CRCs)."""
        return list(iter_event_dicts(self.events_path))

    def read_payload(self, event: dict[str, Any]) -> dict[str, Any]:
        """Load the JSON payload object for an event envelope."""
        raw = self.read_blob(event["blob_ref"])
        result: dict[str, Any] = json.loads(raw.decode("utf-8"))
        return result

    def _put_blob_unlocked(self, data: bytes) -> str:
        digest_hex = hashlib.sha256(data).hexdigest()
        ref = f"sha256:{digest_hex}"
        path = self.blobs_dir / digest_hex
        gz_path = self.blobs_dir / f"{digest_hex}.gz"
        self._logical_bytes += len(data)
        if path.exists() or gz_path.exists():
            return ref
        if len(data) > GZIP_THRESHOLD:
            # mtime=0 keeps compressed bytes platform-stable.
            compressed = gzip.compress(data, mtime=0)
            gz_path.write_bytes(compressed)
            self._stored_bytes += len(compressed)
        else:
            path.write_bytes(data)
            self._stored_bytes += len(data)
        return ref

    def _append_body_unlocked(self, body: bytes) -> None:
        line = encode_event_line(body)
        with self.events_path.open("ab") as fh:
            fh.write(line)
        self._event_bodies.append(body)
        self._event_count += 1
        # Keep seq counter ahead of highest seen seq when loading isn't used.
        envelope = json.loads(body.decode("utf-8"))
        seq = int(envelope["seq"])
        if seq + 1 > self._seq:
            self._seq = seq + 1


def _read_blob_file(blobs_dir: Path, ref: str) -> bytes:
    if not ref.startswith("sha256:"):
        raise ValueError(f"unsupported blob ref: {ref!r}")
    digest_hex = ref.removeprefix("sha256:")
    gz_path = blobs_dir / f"{digest_hex}.gz"
    path = blobs_dir / digest_hex
    if gz_path.exists():
        data = gzip.decompress(gz_path.read_bytes())
    elif path.exists():
        data = path.read_bytes()
    else:
        raise FileNotFoundError(f"missing blob {ref}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != digest_hex:
        raise ValueError(
            f"blob content digest mismatch for {ref}: actual=sha256:{actual}"
        )
    return data


def iter_event_dicts(events_path: Path) -> Iterator[dict[str, Any]]:
    if not events_path.exists():
        return
    for line in events_path.read_bytes().splitlines(keepends=True):
        if not line.strip():
            continue
        body = decode_event_line(line if line.endswith(b"\n") else line + b"\n")
        yield json.loads(body.decode("utf-8"))


def load_run(root: Path) -> FrozenRun:
    """Load a run as a frozen immutable object (verifies CRCs)."""
    root = Path(root)
    events_path = root / "events.jsonl"
    manifest_path = root / "manifest.json"
    bodies: list[bytes] = []
    events: list[Event] = []
    for line in events_path.read_bytes().splitlines(keepends=True):
        if not line.strip():
            continue
        body = decode_event_line(line if line.endswith(b"\n") else line + b"\n")
        bodies.append(body)
        data = json.loads(body.decode("utf-8"))
        events.append(
            Event(
                run_id=data["run_id"],
                seq=int(data["seq"]),
                logical_parent_seq=data["logical_parent_seq"],
                kind=EventKind(data["kind"]),
                payload_digest=data["payload_digest"],
                blob_ref=data["blob_ref"],
            )
        )
    if manifest_path.exists():
        manifest_obj = json.loads(manifest_path.read_bytes().decode("utf-8"))
        manifest = Manifest(
            run_id=manifest_obj["run_id"],
            created_at=manifest_obj["created_at"],
            canon_version=manifest_obj["canon_version"],
            record_version=manifest_obj["record_version"],
            event_count=int(manifest_obj["event_count"]),
            root_digest=manifest_obj["root_digest"],
        )
    else:
        manifest = Manifest(
            run_id=events[0].run_id if events else root.name,
            created_at="",
            canon_version=CANON_VERSION,
            record_version=RECORD_FORMAT_VERSION,
            event_count=len(events),
            root_digest=root_digest_for_bodies(bodies),
        )
    return FrozenRun(
        run_id=manifest.run_id,
        root=root,
        manifest=manifest,
        events=tuple(events),
    )


def verify_run(root: Path) -> VerifyResult:
    """Recompute every CRC and the root digest; check blob integrity."""
    root = Path(root)
    errors: list[str] = []
    run_id = root.name
    events_path = root / "events.jsonl"
    manifest_path = root / "manifest.json"
    blobs_dir = root / "blobs"

    if not events_path.exists():
        return VerifyResult(False, run_id, ("missing events.jsonl",))

    bodies: list[bytes] = []
    try:
        lines = events_path.read_bytes().splitlines(keepends=True)
    except OSError as exc:
        return VerifyResult(False, run_id, (f"cannot read events.jsonl: {exc}",))

    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            body = decode_event_line(line if line.endswith(b"\n") else line + b"\n")
        except ValueError as exc:
            errors.append(f"events.jsonl line {index}: {exc}")
            continue
        bodies.append(body)
        try:
            envelope = json.loads(body.decode("utf-8"))
            ref = envelope["blob_ref"]
            payload = _read_blob_file(blobs_dir, ref)
            expected_digest = envelope["payload_digest"]
            # Payload blob is JCS JSON; re-digest the parsed object.
            payload_obj = json.loads(payload.decode("utf-8"))
            actual_digest = digest(payload_obj)
            if actual_digest != expected_digest:
                errors.append(
                    f"seq={envelope.get('seq')}: payload_digest mismatch"
                )
            if envelope.get("run_id"):
                run_id = str(envelope["run_id"])
        except Exception as exc:  # noqa: BLE001 — collect all verify errors
            errors.append(f"events.jsonl line {index}: blob/payload error: {exc}")

    computed_root = root_digest_for_bodies(bodies)
    if not manifest_path.exists():
        errors.append("missing manifest.json")
    else:
        try:
            manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))
            run_id = str(manifest.get("run_id", run_id))
            if int(manifest.get("event_count", -1)) != len(bodies):
                errors.append(
                    "manifest event_count mismatch: "
                    f"{manifest.get('event_count')} != {len(bodies)}"
                )
            if manifest.get("root_digest") != computed_root:
                errors.append(
                    "root digest mismatch: "
                    f"manifest={manifest.get('root_digest')} "
                    f"computed={computed_root}"
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"manifest.json error: {exc}")

    # Probe every blob file on disk for bitrot even if unreferenced.
    if blobs_dir.exists():
        for path in sorted(blobs_dir.iterdir()):
            if not path.is_file():
                continue
            name = path.name
            if name.endswith(".gz"):
                digest_hex = name[: -len(".gz")]
                try:
                    data = gzip.decompress(path.read_bytes())
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"blob {name}: gzip error: {exc}")
                    continue
            else:
                digest_hex = name
                data = path.read_bytes()
            actual = hashlib.sha256(data).hexdigest()
            if actual != digest_hex:
                errors.append(
                    f"blob {name}: content digest mismatch "
                    f"(expected sha256:{digest_hex}, got sha256:{actual})"
                )

    return VerifyResult(ok=not errors, run_id=run_id, errors=tuple(errors))
