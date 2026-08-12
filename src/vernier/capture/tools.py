"""Local Python tool capture (non-HTTP) and replay stubs."""

from __future__ import annotations

import contextlib
import functools
import inspect
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any, TypeVar

from vernier.canonical import digest
from vernier.canonical._jcs import serialize_jcs
from vernier.capture.recorder import (
    emit_capture_gap,
    get_store,
    safe_append,
)
from vernier.record.schema import EventKind
from vernier.replay.catalog import tool_call_key
from vernier.replay.session import (
    get_catalog,
    is_live_diff,
    record_replay_miss,
)

F = TypeVar("F", bound=Callable[..., Any])


def tool(func: F) -> F:
    """Decorator that records ToolCall / ToolResult / ToolError around ``func``."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        store = get_store()
        catalog = get_catalog()
        if store is None and catalog is None:
            return func(*args, **kwargs)

        args_obj = _jsonable_args(args, kwargs)
        args_digest = digest(args_obj)
        name = func.__qualname__
        key = tool_call_key(name=name, args_digest=args_digest)

        seq = store.next_seq() if store is not None else -1
        if store is not None:
            try:
                payload = {
                    "channel": "function",
                    "name": name,
                    "args_digest": args_digest,
                }
                safe_append(
                    store,
                    seq=seq,
                    kind=EventKind.TOOL_CALL,
                    payload=payload,
                )
            except Exception as exc:  # noqa: BLE001
                emit_capture_gap(
                    store,
                    reason="tool_call_record_failed",
                    detail={"error_type": type(exc).__name__, "seq": seq},
                    logical_parent_seq=seq,
                )

        if catalog is not None:
            stub = catalog.take_tool(key)
            if stub is None:
                detail = f"no stub for tool {name!r} args_digest={args_digest}"
                if is_live_diff():
                    # Model chose tool/args absent from the baseline record.
                    # Execute live so the divergence is classifiable (D04/D05/D06)
                    # rather than collapsing into a ReplayToolMiss harness fault.
                    pass
                else:
                    raise record_replay_miss(
                        channel="tool",
                        detail=detail,
                        logical_parent_seq=seq if seq >= 0 else None,
                    )
            else:
                if not stub.ok:
                    if store is not None:
                        with contextlib.suppress(Exception):
                            safe_append(
                                store,
                                seq=store.next_seq(),
                                kind=EventKind.TOOL_ERROR,
                                payload={
                                    "channel": "function",
                                    "name": name,
                                    "error_type": stub.error_type or "Exception",
                                    "error": stub.error or "",
                                },
                                logical_parent_seq=seq,
                            )
                    raise RuntimeError(stub.error or stub.error_type or "tool error")
                if store is not None:
                    try:
                        result_obj = _jsonable(stub.result)
                        safe_append(
                            store,
                            seq=store.next_seq(),
                            kind=EventKind.TOOL_RESULT,
                            payload={
                                "channel": "function",
                                "name": name,
                                "result_digest": digest(result_obj),
                            },
                            logical_parent_seq=seq,
                            blob_data=serialize_jcs(result_obj),
                        )
                    except Exception as exc:  # noqa: BLE001
                        emit_capture_gap(
                            store,
                            reason="tool_result_record_failed",
                            detail={"error_type": type(exc).__name__, "seq": seq},
                            logical_parent_seq=seq,
                        )
                return stub.result

        assert store is not None
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            with contextlib.suppress(Exception):
                safe_append(
                    store,
                    seq=store.next_seq(),
                    kind=EventKind.TOOL_ERROR,
                    payload={
                        "channel": "function",
                        "name": name,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                    logical_parent_seq=seq,
                )
            raise

        try:
            result_obj = _jsonable(result)
            safe_append(
                store,
                seq=store.next_seq(),
                kind=EventKind.TOOL_RESULT,
                payload={
                    "channel": "function",
                    "name": name,
                    "result_digest": digest(result_obj),
                },
                logical_parent_seq=seq,
                blob_data=serialize_jcs(result_obj),
            )
        except Exception as exc:  # noqa: BLE001
            emit_capture_gap(
                store,
                reason="tool_result_record_failed",
                detail={"error_type": type(exc).__name__, "seq": seq},
                logical_parent_seq=seq,
            )
        return result

    return wrapper  # type: ignore[return-value]


def wrap_tools(
    registry: Mapping[str, Callable[..., Any]],
) -> dict[str, Callable[..., Any]]:
    """Return a new registry with each callable wrapped by :func:`tool`."""
    out: dict[str, Callable[..., Any]] = {}
    for name, fn in registry.items():
        if callable(fn):
            out[name] = tool(fn)
        else:
            out[name] = fn
    return out


def wrap_tools_inplace(
    registry: MutableMapping[str, Callable[..., Any]],
) -> MutableMapping[str, Callable[..., Any]]:
    """Wrap tools in a mutable registry in place."""
    for name, fn in list(registry.items()):
        if callable(fn):
            registry[name] = tool(fn)
    return registry


def _jsonable_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return {
        "args": [_jsonable(a) for a in args],
        "kwargs": {k: _jsonable(v) for k, v in kwargs.items()},
    }


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if inspect.isroutine(value) or inspect.isclass(value):
        return getattr(value, "__qualname__", repr(value))
    return repr(value)
