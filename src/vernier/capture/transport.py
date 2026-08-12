"""Transport-level HTTP capture for httpx and requests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from vernier.capture.bodies import body_digest
from vernier.capture.recorder import (
    emit_capture_gap,
    get_store,
    record_http_request,
    record_http_response,
)
from vernier.record.store import RecordStore
from vernier.replay.catalog import http_request_key
from vernier.replay.overrides import apply_live_overrides
from vernier.replay.request_parity import (
    HarnessRequestBodyMismatch,
    check_live_diff_request_parity,
)
from vernier.replay.session import get_catalog, record_replay_miss, stub_http
from vernier.classify.types import HARNESS_FAULT_REQUEST_BODY_MISMATCH


def _headers_to_dict(headers: Any) -> dict[str, str]:
    items: dict[str, str] = {}
    for key, value in headers.items():
        items[str(key)] = value if isinstance(value, str) else str(value)
    return items


class ReplayByteStream(httpx.SyncByteStream):
    """Re-emit recorded stream chunks verbatim."""

    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks

    def close(self) -> None:
        return


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


def _replay_httpx_response(
    request: httpx.Request,
    *,
    store: RecordStore | None,
    seq: int,
    method: str,
    url: str,
    body: bytes,
) -> httpx.Response:
    catalog = get_catalog()
    assert catalog is not None
    key = http_request_key(
        method=method,
        url=url,
        body_digest=body_digest(body),
    )
    stub = catalog.take_http(key)
    if stub is None:
        raise record_replay_miss(
            channel="http",
            detail=f"no stub for {method} {url} key={key}",
            logical_parent_seq=seq if seq >= 0 else None,
        )
    if store is not None:
        record_http_response(
            store,
            request_seq=seq,
            status=stub.status,
            headers=stub.headers,
            body=stub.body,
            chunks=list(stub.chunks) if stub.stream else None,
        )
    header_list = [(k, v) for k, v in stub.headers.items()]
    if stub.stream:
        return httpx.Response(
            status_code=stub.status,
            headers=header_list,
            stream=ReplayByteStream(stub.chunks),
            request=request,
        )
    return httpx.Response(
        status_code=stub.status,
        headers=header_list,
        content=stub.body,
        request=request,
    )


class VernierHttpxTransport(httpx.BaseTransport):
    """httpx transport that records request/response then passes through."""

    def __init__(self, wrapped: httpx.BaseTransport | None = None) -> None:
        self._wrapped = wrapped or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        store = get_store()
        catalog = get_catalog()
        if store is None and catalog is None:
            return self._wrapped.handle_request(request)

        seq = store.next_seq() if store is not None else -1
        method = request.method
        url = str(request.url)
        headers = _headers_to_dict(request.headers)
        body = b""
        if request.content is not None:
            body = request.content

        # Live-diff: rewrite model/prompt before record + live hop.
        if catalog is not None and not stub_http():
            overridden = apply_live_overrides(body)
            if overridden != body:
                body = overridden
                # Drop stale Content-Length — body size changes when model/prompt
                # is rewritten (httpx would otherwise send the old length).
                new_headers = [
                    (k, v)
                    for k, v in request.headers.multi_items()
                    if k.lower() != "content-length"
                ]
                request = httpx.Request(
                    method=request.method,
                    url=request.url,
                    headers=new_headers,
                    content=body,
                    extensions=request.extensions,
                )

            baseline_body = catalog.take_llm_request_body()
            if baseline_body is not None:
                turn = catalog.llm_request_turn
                mismatches = check_live_diff_request_parity(
                    body,
                    baseline_body,
                    turn_index=turn,
                )
                if mismatches:
                    detail = (
                        f"{HARNESS_FAULT_REQUEST_BODY_MISMATCH} "
                        f"turn={turn}: " + "; ".join(mismatches)
                    )
                    record_replay_miss(
                        channel="harness",
                        detail=detail,
                        logical_parent_seq=seq if seq >= 0 else None,
                    )
                    raise HarnessRequestBodyMismatch(detail)
            catalog.llm_request_turn += 1

        if store is not None:
            try:
                record_http_request(
                    store,
                    seq=seq,
                    method=method,
                    url=url,
                    headers=headers,
                    body=body,
                )
            except Exception as exc:  # noqa: BLE001
                emit_capture_gap(
                    store,
                    reason="shim_request_failed",
                    detail={"error_type": type(exc).__name__, "seq": seq},
                    logical_parent_seq=seq,
                )

        if catalog is not None and stub_http():
            return _replay_httpx_response(
                request,
                store=store,
                seq=seq,
                method=method,
                url=url,
                body=body,
            )

        assert store is not None
        try:
            response = self._wrapped.handle_request(request)
        except Exception as exc:
            emit_capture_gap(
                store,
                reason="transport_error",
                detail={
                    "seq": seq,
                    "method": method,
                    "url": url,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                },
                logical_parent_seq=seq,
            )
            raise

        try:
            resp_headers = _headers_to_dict(response.headers)
            stream = response.stream
            if isinstance(stream, httpx.SyncByteStream):
                capturing = CapturingByteStream(
                    stream,
                    store=store,
                    request_seq=seq,
                    status=response.status_code,
                    headers=resp_headers,
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
                headers=resp_headers,
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
            for chunk in original_iter(chunk_size=chunk_size, decode_unicode=False):
                data = chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8")
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
                    yield data.decode(response.encoding or "utf-8", errors="replace")
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


def _replay_requests_response(
    *,
    store: RecordStore | None,
    seq: int,
    method: str,
    url: str,
    body: bytes,
    want_stream: bool,
) -> Any:
    import requests

    catalog = get_catalog()
    assert catalog is not None
    key = http_request_key(
        method=method,
        url=url,
        body_digest=body_digest(body),
    )
    stub = catalog.take_http(key)
    if stub is None:
        raise record_replay_miss(
            channel="http",
            detail=f"no stub for {method} {url} key={key}",
            logical_parent_seq=seq if seq >= 0 else None,
        )
    if store is not None:
        record_http_response(
            store,
            request_seq=seq,
            status=stub.status,
            headers=stub.headers,
            body=stub.body,
            chunks=list(stub.chunks) if stub.stream else None,
        )

    response = requests.models.Response()
    response.status_code = stub.status
    response.headers.update(stub.headers)
    response._content = stub.body  # noqa: SLF001
    response.url = url
    response.encoding = "utf-8"

    if want_stream or stub.stream:
        chunk_list = list(stub.chunks) if stub.chunks else [stub.body]

        def iter_content(
            chunk_size: int = 1, decode_unicode: bool = False
        ) -> Iterator[Any]:
            del chunk_size
            for data in chunk_list:
                if decode_unicode:
                    yield data.decode("utf-8", errors="replace")
                else:
                    yield data

        object.__setattr__(response, "iter_content", iter_content)
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
        catalog = get_catalog()
        if store is None and catalog is None:
            return original_send(self, request, **kwargs)

        seq = store.next_seq() if store is not None else -1
        body = request.body or b""
        if isinstance(body, str):
            body = body.encode("utf-8")

        if catalog is not None and not stub_http():
            overridden = apply_live_overrides(body)
            if overridden != body:
                body = overridden
                request.body = body
                # Keep Content-Length aligned with the rewritten body.
                if "Content-Length" in request.headers:
                    request.headers["Content-Length"] = str(len(body))
                elif "content-length" in request.headers:
                    request.headers["content-length"] = str(len(body))

            baseline_body = catalog.take_llm_request_body()
            if baseline_body is not None:
                turn = catalog.llm_request_turn
                mismatches = check_live_diff_request_parity(
                    body,
                    baseline_body,
                    turn_index=turn,
                )
                if mismatches:
                    detail = (
                        f"{HARNESS_FAULT_REQUEST_BODY_MISMATCH} "
                        f"turn={turn}: " + "; ".join(mismatches)
                    )
                    record_replay_miss(
                        channel="harness",
                        detail=detail,
                        logical_parent_seq=seq if seq >= 0 else None,
                    )
                    raise HarnessRequestBodyMismatch(detail)
            catalog.llm_request_turn += 1

        if store is not None:
            try:
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

        if catalog is not None and stub_http():
            return _replay_requests_response(
                store=store,
                seq=seq,
                method=request.method,
                url=request.url,
                body=body,
                want_stream=bool(kwargs.get("stream", False)),
            )

        assert store is not None
        want_stream = bool(kwargs.get("stream", False))
        kwargs = dict(kwargs)
        kwargs["stream"] = True
        try:
            response = original_send(self, request, **kwargs)
        except Exception as exc:
            emit_capture_gap(
                store,
                reason="transport_error",
                detail={
                    "seq": seq,
                    "url": request.url,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                },
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
