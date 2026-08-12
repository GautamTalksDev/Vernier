"""Active capture session bound to a :class:`RecordStore`."""

from __future__ import annotations

import contextlib
import os
import traceback
from pathlib import Path
from typing import Any

from vernier.capture.bodies import (
    TRUNCATION_MARKER,
    body_digest,
    check_body_size,
    digest_raw,
)
from vernier.capture.redaction import redact_body_bytes, redact_headers, redact_url
from vernier.record.schema import EventKind
from vernier.record.store import RecordStore

_ENV_RUN_DIR = "VERNIER_RUN_DIR"
_ENV_RUN_ID = "VERNIER_RUN_ID"
_ENV_ACTIVE = "VERNIER_CAPTURE"
_ENV_COMMAND = "VERNIER_COMMAND_JSON"
_ENV_CWD = "VERNIER_CWD"

_active: RecordStore | None = None


def is_active() -> bool:
    return _active is not None


def get_store() -> RecordStore | None:
    return _active


def activate_from_env() -> RecordStore | None:
    """Activate capture if ``VERNIER_CAPTURE`` is set."""
    global _active
    if os.environ.get(_ENV_ACTIVE) != "1":
        return None
    run_dir = os.environ.get(_ENV_RUN_DIR)
    run_id = os.environ.get(_ENV_RUN_ID)
    if not run_dir or not run_id:
        return None
    index_env = os.environ.get("VERNIER_INDEX")
    if index_env:
        index_path: Path | None = Path(index_env)
    else:
        runs_parent = Path(run_dir).resolve().parent
        if runs_parent.name == "runs":
            index_path = runs_parent.parent / "index.sqlite"
        else:
            index_path = runs_parent / "index.sqlite"

    command: list[str] = []
    raw_cmd = os.environ.get(_ENV_COMMAND)
    if raw_cmd:
        import json

        parsed = json.loads(raw_cmd)
        if isinstance(parsed, list):
            command = [str(x) for x in parsed]
    cwd = os.environ.get(_ENV_CWD, "")

    store = RecordStore(
        Path(run_dir),
        run_id,
        index_path=index_path,
        command=command,
        cwd=cwd,
    )
    _active = store
    safe_append(
        store,
        seq=store.next_seq(),
        kind=EventKind.RUN_START,
        payload={
            "run_id": run_id,
            "command": command,
            "cwd": cwd,
        },
    )
    return store


def deactivate() -> None:
    global _active
    store = _active
    if store is None:
        return
    safe_append(
        store,
        seq=store.next_seq(),
        kind=EventKind.RUN_END,
        payload={"run_id": store.run_id},
    )
    with contextlib.suppress(Exception):
        store.finalize()
    _active = None
    from vernier.replay.session import deactivate_replay

    deactivate_replay()


def activate_store(store: RecordStore) -> None:
    """Activate an explicit store (tests)."""
    global _active
    _active = store


def safe_append(
    store: RecordStore,
    *,
    seq: int,
    kind: EventKind,
    payload: dict[str, Any],
    logical_parent_seq: int | None = None,
    blob_data: bytes | None = None,
) -> None:
    """Append an event; never raise to the caller."""
    try:
        store.append_event(
            seq=seq,
            kind=kind,
            payload=payload,
            logical_parent_seq=logical_parent_seq,
            blob_data=blob_data,
        )
    except Exception:  # noqa: BLE001 — capture must never crash the agent
        with contextlib.suppress(Exception):
            store.append_event(
                seq=store.next_seq(),
                kind=EventKind.CAPTURE_GAP,
                payload={
                    "reason": "append_failed",
                    "failed_kind": kind.value,
                    "failed_seq": seq,
                    "error": "internal_append_error",
                },
            )


def emit_capture_gap(
    store: RecordStore,
    *,
    reason: str,
    detail: dict[str, Any] | None = None,
    logical_parent_seq: int | None = None,
) -> None:
    payload: dict[str, Any] = {"reason": reason}
    if detail:
        payload["detail"] = detail
    safe_append(
        store,
        seq=store.next_seq(),
        kind=EventKind.CAPTURE_GAP,
        payload=payload,
        logical_parent_seq=logical_parent_seq,
    )


def record_http_request(
    store: RecordStore,
    *,
    seq: int,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
) -> None:
    try:
        safe_url = redact_url(url)
        safe_body = redact_body_bytes(body)
        exceeds, size = check_body_size(safe_body)
        redacted = redact_headers(headers)
        if exceeds:
            payload = {
                "channel": "http",
                "phase": "request",
                "method": method,
                "url": safe_url,
                "headers": redacted,
                "body_truncated": True,
                "body_size": size,
                "truncation": TRUNCATION_MARKER,
            }
            safe_append(
                store,
                seq=seq,
                kind=EventKind.LLM_REQUEST,
                payload=payload,
            )
            emit_capture_gap(
                store,
                reason=TRUNCATION_MARKER,
                detail={"phase": "request", "body_size": size, "seq": seq},
                logical_parent_seq=seq,
            )
            return
        payload = {
            "channel": "http",
            "phase": "request",
            "method": method,
            "url": safe_url,
            "headers": redacted,
            "body_truncated": False,
            "body_size": size,
            "body_digest": body_digest(safe_body),
        }
        safe_append(
            store,
            seq=seq,
            kind=EventKind.LLM_REQUEST,
            payload=payload,
            blob_data=safe_body,
        )
    except Exception as exc:  # noqa: BLE001
        emit_capture_gap(
            store,
            reason="shim_request_failed",
            detail={"error_type": type(exc).__name__, "seq": seq},
            logical_parent_seq=seq,
        )


def record_http_response(
    store: RecordStore,
    *,
    request_seq: int,
    status: int,
    headers: dict[str, str],
    body: bytes,
    chunks: list[bytes] | None,
) -> None:
    try:
        safe_body = redact_body_bytes(body)
        exceeds, size = check_body_size(safe_body)
        redacted = redact_headers(headers)
        stream_meta: list[dict[str, Any]] = []
        if chunks is not None:
            for index, chunk in enumerate(chunks):
                stream_meta.append(
                    {
                        "index": index,
                        "size": len(chunk),
                        "digest": digest_raw(chunk),
                    }
                )
        if exceeds:
            payload = {
                "channel": "http",
                "phase": "response",
                "status": status,
                "headers": redacted,
                "body_truncated": True,
                "body_size": size,
                "truncation": TRUNCATION_MARKER,
                "stream": chunks is not None,
                "chunks": stream_meta,
            }
            safe_append(
                store,
                seq=store.next_seq(),
                kind=EventKind.LLM_RESPONSE,
                payload=payload,
                logical_parent_seq=request_seq,
            )
            emit_capture_gap(
                store,
                reason=TRUNCATION_MARKER,
                detail={
                    "phase": "response",
                    "body_size": size,
                    "request_seq": request_seq,
                },
                logical_parent_seq=request_seq,
            )
            return
        payload = {
            "channel": "http",
            "phase": "response",
            "status": status,
            "headers": redacted,
            "body_truncated": False,
            "body_size": size,
            "body_digest": body_digest(safe_body),
            "stream": chunks is not None,
            "chunks": stream_meta,
        }
        safe_append(
            store,
            seq=store.next_seq(),
            kind=EventKind.LLM_RESPONSE,
            payload=payload,
            logical_parent_seq=request_seq,
            blob_data=safe_body,
        )
    except Exception as exc:  # noqa: BLE001
        emit_capture_gap(
            store,
            reason="shim_response_failed",
            detail={
                "error_type": type(exc).__name__,
                "request_seq": request_seq,
                "traceback": traceback.format_exc()[-500:],
            },
            logical_parent_seq=request_seq,
        )
