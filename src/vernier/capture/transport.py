"""Transport-level HTTP capture for httpx and requests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from vernier.capture.recorder import (
    emit_capture_gap,
    get_store,
    record_http_request,
    record_http_response,
)
from vernier.record.store import RecordStore


def _headers_to_dict(headers: Any) -> dict[str, str]:
    items: dict[str, str] = {}
    for key, value in headers.items():
        items[str(key)] = value if isinstance(value, str) else str(value)
    return items


class CapturingByteStream(httpx.SyncByteStream):
    """Wrap an httpx stream; record each chunk with its arrival index.

    For ``text/event-stream``, frames are split on ``\\n\\n`` so SSE replay can
    re-emit event-for-event even when the socket coalesces reads.
    """

    def __init__(
        self,
        inner: httpx.SyncByteStream,
        *,
        store: RecordStore,
        request_seq: int,
        status: int,
        headers: dict[str, str],
    ) -> None:
        self._inner = inner
        self._store = store
        self._request_seq = request_seq
        self._status = status
        self._headers = headers
        self._chunks: list[bytes] = []
        self._index = 0
        self._finalized = False
        content_type = headers.get("content-type", headers.get("Content-Type", ""))
        self._is_sse = "text/event-stream" in content_type.lower()

    def __iter__(self) -> Iterator[bytes]:
        try:
            if self._is_sse:
                yield from self._iter_sse()
            else:
                for chunk in self._inner:
                    data = chunk if isinstance(chunk, bytes) else bytes(chunk)
                    self._observe(data)
                    yield data
        finally:
            self._finalize()

    def _iter_sse(self) -> Iterator[bytes]:
        buffer = b""
        for chunk in self._inner:
            data = chunk if isinstance(chunk, bytes) else bytes(chunk)
            buffer += data
            while b"\n\n" in buffer:
                frame, buffer = buffer.split(b"\n\n", 1)
                frame_bytes = frame + b"\n\n"
                self._observe(frame_bytes)
                yield frame_bytes
        if buffer:
            self._observe(buffer)
            yield buffer

    def _observe(self, data: bytes) -> None:
        self._chunks.append(data)
        try:
            from vernier.canonical._normalize import MAX_VALUE_BYTES

            if len(data) > MAX_VALUE_BYTES:
                emit_capture_gap(
                    self._store,
                    reason="body_exceeds_8mib",
                    detail={
                        "phase": "chunk",
                        "request_seq": self._request_seq,
                        "index": self._index,
                        "body_size": len(data),
                    },
                    logical_parent_seq=self._request_seq,
                )
            else:
                self._store.write_blob(
                    data,
                    label=f"chunk:{self._request_seq}:{self._index}",
                )
        except Exception:  # noqa: BLE001
            emit_capture_gap(
                self._store,
                reason="chunk_record_failed",
                detail={
                    "request_seq": self._request_seq,
                    "index": self._index,
                },
                logical_parent_seq=self._request_seq,
            )
        self._index += 1

    def close(self) -> None:
        try:
            self._inner.close()
        finally:
            self._finalize()

    def _finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        body = b"".join(self._chunks)
        record_http_response(
            self._store,
            request_seq=self._request_seq,
            status=self._status,
            headers=self._headers,
            body=body,
            chunks=list(self._chunks),
        )


class VernierHttpxTransport(httpx.BaseTransport):
    """httpx transport that records request/response then passes through."""

    def __init__(self, wrapped: httpx.BaseTransport | None = None) -> None:
        self._wrapped = wrapped or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        store = get_store()
        if store is None:
            return self._wrapped.handle_request(request)

        seq = store.next_seq()
        try:
            body = b""
            if request.content is not None:
                body = request.content
            record_http_request(
                store,
                seq=seq,
                method=request.method,
                url=str(request.url),
                headers=_headers_to_dict(request.headers),
                body=body,
            )
        except Exception as exc:  # noqa: BLE001
            emit_capture_gap(
                store,
                reason="shim_request_failed",
                detail={"error_type": type(exc).__name__, "seq": seq},
                logical_parent_seq=seq,
            )

        try:
            response = self._wrapped.handle_request(request)
        except Exception:
            emit_capture_gap(
                store,
                reason="transport_error",
                detail={
                    "seq": seq,
                    "method": request.method,
                    "url": str(request.url),
                },
                logical_parent_seq=seq,
            )
            raise

        try:
            headers = _headers_to_dict(response.headers)
            stream = response.stream
            if isinstance(stream, httpx.SyncByteStream):
                capturing = CapturingByteStream(
                    stream,
                    store=store,
                    request_seq=seq,
                    status=response.status_code,
                    headers=headers,
                )
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    stream=capturing,
                    request=request,
                    extensions=response.extensions,
                )
            content = response.read()
            record_http_response(
                store,
                request_seq=seq,
                status=response.status_code,
                headers=headers,
                body=content,
                chunks=None,
            )
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=content,
                request=request,
                extensions=response.extensions,
            )
        except Exception as exc:  # noqa: BLE001
            emit_capture_gap(
                store,
                reason="shim_response_wrap_failed",
                detail={"error_type": type(exc).__name__, "seq": seq},
                logical_parent_seq=seq,
            )
            return response

    def close(self) -> None:
        self._wrapped.close()


def _wrap_requests_response(
    response: Any,
    *,
    store: RecordStore,
    request_seq: int,
    stream: bool,
) -> Any:
    if not stream:
        try:
            content = response.content
            if not isinstance(content, bytes):
                content = bytes(content)
            record_http_response(
                store,
                request_seq=request_seq,
                status=int(response.status_code),
                headers=_headers_to_dict(response.headers),
                body=content,
                chunks=None,
            )
        except Exception as exc:  # noqa: BLE001
            emit_capture_gap(
                store,
                reason="shim_response_failed",
                detail={
                    "error_type": type(exc).__name__,
                    "request_seq": request_seq,
                },
                logical_parent_seq=request_seq,
            )
        return response

    chunks: list[bytes] = []
    recorded = {"done": False}
    original_iter = response.iter_content

    def iter_content(
        chunk_size: int = 1, decode_unicode: bool = False
    ) -> Iterator[Any]:
        try:
            for chunk in original_iter(
                chunk_size=chunk_size, decode_unicode=False
            ):
                data = (
                    chunk
                    if isinstance(chunk, bytes)
                    else str(chunk).encode("utf-8")
                )
                chunks.append(data)
                try:
                    store.write_blob(
                        data, label=f"chunk:{request_seq}:{len(chunks) - 1}"
                    )
                except Exception:  # noqa: BLE001
                    emit_capture_gap(
                        store,
                        reason="chunk_record_failed",
                        detail={
                            "request_seq": request_seq,
                            "index": len(chunks) - 1,
                        },
                        logical_parent_seq=request_seq,
                    )
                if decode_unicode:
                    yield data.decode(
                        response.encoding or "utf-8", errors="replace"
                    )
                else:
                    yield data
        finally:
            if not recorded["done"]:
                recorded["done"] = True
                record_http_response(
                    store,
                    request_seq=request_seq,
                    status=int(response.status_code),
                    headers=_headers_to_dict(response.headers),
                    body=b"".join(chunks),
                    chunks=list(chunks),
                )

    response.iter_content = iter_content
    return response


def install_httpx_hooks() -> None:
    """Patch httpx.Client constructors to inject our transport."""
    import httpx as httpx_mod

    if getattr(httpx_mod, "_vernier_patched", False):
        return

    original_client_init = httpx_mod.Client.__init__

    def client_init(
        self: httpx.Client,
        *args: Any,
        transport: httpx.BaseTransport | None = None,
        **kwargs: Any,
    ) -> None:
        if isinstance(transport, VernierHttpxTransport):
            wrapped: httpx.BaseTransport = transport
        else:
            wrapped = VernierHttpxTransport(transport)
        original_client_init(self, *args, transport=wrapped, **kwargs)

    httpx_mod.Client.__init__ = client_init  # type: ignore[method-assign]
    httpx_mod._vernier_patched = True  # type: ignore[attr-defined]


def install_requests_hooks() -> None:
    """Patch requests.Session to mount a recording HTTPAdapter."""
    import requests
    from requests.adapters import HTTPAdapter

    if getattr(requests, "_vernier_patched", False):
        return

    original_send = HTTPAdapter.send

    def send(self: HTTPAdapter, request: Any, **kwargs: Any) -> Any:
        store = get_store()
        if store is None:
            return original_send(self, request, **kwargs)

        seq = store.next_seq()
        try:
            body = request.body or b""
            if isinstance(body, str):
                body = body.encode("utf-8")
            record_http_request(
                store,
                seq=seq,
                method=request.method,
                url=request.url,
                headers=_headers_to_dict(request.headers),
                body=body,
            )
        except Exception as exc:  # noqa: BLE001
            emit_capture_gap(
                store,
                reason="shim_request_failed",
                detail={"error_type": type(exc).__name__, "seq": seq},
                logical_parent_seq=seq,
            )

        want_stream = bool(kwargs.get("stream", False))
        kwargs = dict(kwargs)
        kwargs["stream"] = True
        try:
            response = original_send(self, request, **kwargs)
        except Exception:
            emit_capture_gap(
                store,
                reason="transport_error",
                detail={"seq": seq, "url": request.url},
                logical_parent_seq=seq,
            )
            raise

        return _wrap_requests_response(
            response,
            store=store,
            request_seq=seq,
            stream=want_stream,
        )

    HTTPAdapter.send = send  # type: ignore[assignment]
    requests._vernier_patched = True  # type: ignore[attr-defined]


# Back-compat name used in docs / tests.
class VernierHTTPAdapter:
    """Marker type documenting the requests integration (hooks patch send)."""

    pass
