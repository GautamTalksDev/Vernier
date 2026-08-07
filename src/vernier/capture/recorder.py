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
from vernier.capture.redaction import redact_headers
from vernier.record.schema import EventKind
from vernier.record.store import RecordStore

_ENV_RUN_DIR = "VERNIER_RUN_DIR"
_ENV_RUN_ID = "VERNIER_RUN_ID"
_ENV_ACTIVE = "VERNIER_CAPTURE"

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
    store = RecordStore(Path(run_dir), run_id)
    _active = store
    safe_append(
        store,
        seq=store.next_seq(),
        kind=EventKind.RUN_START,
        payload={"run_id": run_id},
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
    _active = None


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
        exceeds, size = check_body_size(body)
        redacted = redact_headers(headers)
        if exceeds:
            payload = {
                "channel": "http",
                "phase": "request",
                "method": method,
                "url": url,
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
            "url": url,
            "headers": redacted,
            "body_truncated": False,
            "body_size": size,
            "body_digest": body_digest(body),
        }
        safe_append(
            store,
            seq=seq,
            kind=EventKind.LLM_REQUEST,
            payload=payload,
            blob_data=body,
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
        exceeds, size = check_body_size(body)
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
            "body_digest": body_digest(body),
            "stream": chunks is not None,
            "chunks": stream_meta,
        }
        safe_append(
            store,
            seq=store.next_seq(),
            kind=EventKind.LLM_RESPONSE,
            payload=payload,
            logical_parent_seq=request_seq,
            blob_data=body,
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
