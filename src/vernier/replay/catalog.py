"""Load a recorded run into FIFO stub queues for full-stub re-drive."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vernier.canonical import digest
from vernier.record.schema import EventKind
from vernier.record.store import FrozenRun, load_run


@dataclass(frozen=True, slots=True)
class HttpStub:
    """Recorded HTTP response ready to re-emit."""

    status: int
    headers: dict[str, str]
    body: bytes
    stream: bool
    chunks: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class ToolStub:
    """Recorded tool outcome."""

    name: str
    ok: bool
    result: Any = None
    error_type: str | None = None
    error: str | None = None


@dataclass
class ReplayCatalog:
    """FIFO queues of stubs keyed by canonical match digests."""

    source_run_id: str
    source_root: Path
    command: tuple[str, ...]
    cwd: str
    http_by_key: dict[str, deque[HttpStub]] = field(default_factory=dict)
    tool_by_key: dict[str, deque[ToolStub]] = field(default_factory=dict)
    wall_clock: deque[float] = field(default_factory=deque)
    wall_clock_ns: deque[int] = field(default_factory=deque)
    random_floats: deque[float] = field(default_factory=deque)
    env_reads: dict[str, deque[str | None]] = field(default_factory=dict)
    env_keys: frozenset[str] = field(default_factory=frozenset)
    had_wall_clock: bool = False
    had_random: bool = False
    # Ordered baseline chat-completions bodies (live-diff parity checks).
    llm_request_bodies: deque[bytes] = field(default_factory=deque)  # noqa: RUF012
    llm_request_turn: int = 0

    def take_llm_request_body(self) -> bytes | None:
        if not self.llm_request_bodies:
            return None
        return self.llm_request_bodies.popleft()

    def take_http(self, key: str) -> HttpStub | None:
        queue = self.http_by_key.get(key)
        if not queue:
            return None
        return queue.popleft()

    def take_tool(self, key: str) -> ToolStub | None:
        queue = self.tool_by_key.get(key)
        if not queue:
            return None
        return queue.popleft()

    def take_time(self) -> float | None:
        if not self.wall_clock:
            return None
        return self.wall_clock.popleft()

    def take_time_ns(self) -> int | None:
        if not self.wall_clock_ns:
            return None
        return self.wall_clock_ns.popleft()

    def take_random(self) -> float | None:
        if not self.random_floats:
            return None
        return self.random_floats.popleft()

    def take_env(self, name: str) -> tuple[bool, str | None]:
        queue = self.env_reads.get(name)
        if not queue:
            return False, None
        return True, queue.popleft()


def http_request_key(*, method: str, url: str, body_digest: str) -> str:
    """Canonical match key for an LLM/HTTP request.

    Host/port are ignored so re-drives may bind ephemeral ports; path and query
    remain part of the match key together with the body digest.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return digest({"method": method.upper(), "path": path, "body_digest": body_digest})


def tool_call_key(*, name: str, args_digest: str) -> str:
    return digest({"name": name, "args_digest": args_digest})


def load_catalog(root: Path) -> ReplayCatalog:
    """Build a :class:`ReplayCatalog` from a finalized run directory."""
    frozen = load_run(root)
    return catalog_from_run(frozen)


def catalog_from_run(frozen: FrozenRun) -> ReplayCatalog:
    store_root = frozen.root
    from vernier.record.store import RecordStore

    # Use a read-only view via load helpers on the frozen root.
    reader = RecordStore(store_root, frozen.run_id)
    # Store may think it is empty writer; only use read_blob/read_payload.

    http_by_key: dict[str, deque[HttpStub]] = defaultdict(deque)
    tool_by_key: dict[str, deque[ToolStub]] = defaultdict(deque)
    wall_clock: deque[float] = deque()
    wall_clock_ns: deque[int] = deque()
    random_floats: deque[float] = deque()
    env_reads: dict[str, deque[str | None]] = defaultdict(deque)
    llm_request_bodies: deque[bytes] = deque()

    # Index envelopes by seq for parent lookups.
    envelopes = list(reader.read_events())
    payload_by_seq: dict[int, dict[str, Any]] = {}
    for env in envelopes:
        payload_by_seq[int(env["seq"])] = reader.read_payload(env)

    command = frozen.manifest.command
    cwd = frozen.manifest.cwd
    # Prefer RunStart payload if present (older / richer).
    for env in envelopes:
        if env["kind"] == EventKind.RUN_START.value:
            payload = payload_by_seq[int(env["seq"])]
            if payload.get("command"):
                command = tuple(payload["command"])
            if payload.get("cwd"):
                cwd = str(payload["cwd"])
            break

    pending_tool_calls: dict[int, dict[str, Any]] = {}

    for env in envelopes:
        kind = env["kind"]
        seq = int(env["seq"])
        payload = payload_by_seq[seq]

        if kind == EventKind.LLM_REQUEST.value:
            body_ref = payload.get("body_blob_ref")
            if body_ref:
                llm_request_bodies.append(reader.read_blob(body_ref))

        elif kind == EventKind.LLM_RESPONSE.value:
            parent = env.get("logical_parent_seq")
            if parent is None:
                continue
            req = payload_by_seq.get(int(parent))
            if not req or not req.get("body_digest"):
                continue
            key = http_request_key(
                method=str(req.get("method", "GET")),
                url=str(req.get("url", "")),
                body_digest=str(req["body_digest"]),
            )
            body_ref = payload.get("body_blob_ref")
            body = reader.read_blob(body_ref) if body_ref else b""
            chunks_meta = payload.get("chunks") or []
            stream = bool(payload.get("stream"))
            chunk_bytes: list[bytes] = []
            if stream and chunks_meta:
                offset = 0
                for meta in chunks_meta:
                    size = int(meta["size"])
                    chunk_bytes.append(body[offset : offset + size])
                    offset += size
            elif stream:
                chunk_bytes = [body] if body else []
            http_by_key[key].append(
                HttpStub(
                    status=int(payload.get("status", 200)),
                    headers={
                        str(k): str(v)
                        for k, v in (payload.get("headers") or {}).items()
                    },
                    body=body,
                    stream=stream,
                    chunks=tuple(chunk_bytes),
                )
            )

        elif kind == EventKind.TOOL_CALL.value:
            pending_tool_calls[seq] = payload

        elif kind == EventKind.TOOL_RESULT.value:
            parent = env.get("logical_parent_seq")
            call = pending_tool_calls.get(int(parent)) if parent is not None else None
            if call is None:
                continue
            name = str(call.get("name") or payload.get("name") or "")
            args_digest = str(call.get("args_digest") or "")
            key = tool_call_key(name=name, args_digest=args_digest)
            result_ref = payload.get("body_blob_ref") or payload.get("result_blob_ref")
            if result_ref:
                raw = reader.read_blob(result_ref)
                result = json.loads(raw.decode("utf-8"))
            else:
                result = None
            tool_by_key[key].append(ToolStub(name=name, ok=True, result=result))

        elif kind == EventKind.TOOL_ERROR.value:
            parent = env.get("logical_parent_seq")
            call = pending_tool_calls.get(int(parent)) if parent is not None else None
            if call is None:
                continue
            name = str(call.get("name") or payload.get("name") or "")
            args_digest = str(call.get("args_digest") or "")
            key = tool_call_key(name=name, args_digest=args_digest)
            tool_by_key[key].append(
                ToolStub(
                    name=name,
                    ok=False,
                    error_type=str(payload.get("error_type") or "Exception"),
                    error=str(payload.get("error") or ""),
                )
            )

        elif kind == EventKind.WALL_CLOCK.value:
            if "time" in payload:
                wall_clock.append(float(payload["time"]))
            if "time_ns" in payload:
                wall_clock_ns.append(int(payload["time_ns"]))

        elif kind == EventKind.RANDOM.value:
            if "value" in payload:
                random_floats.append(float(payload["value"]))

        elif kind == EventKind.ENV_READ.value:
            name = str(payload.get("name") or "")
            if name:
                env_reads[name].append(payload.get("value"))

    return ReplayCatalog(
        source_run_id=frozen.run_id,
        source_root=store_root,
        command=command,
        cwd=cwd,
        http_by_key=dict(http_by_key),
        tool_by_key=dict(tool_by_key),
        wall_clock=wall_clock,
        wall_clock_ns=wall_clock_ns,
        random_floats=random_floats,
        env_reads=dict(env_reads),
        env_keys=frozenset(env_reads),
        had_wall_clock=bool(wall_clock or wall_clock_ns),
        had_random=bool(random_floats),
        llm_request_bodies=llm_request_bodies,
    )
