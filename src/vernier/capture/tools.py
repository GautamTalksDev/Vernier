"""Local Python tool capture (non-HTTP)."""

from __future__ import annotations

import contextlib
import functools
import inspect
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any, TypeVar

from vernier.canonical import digest
from vernier.capture.recorder import (
    emit_capture_gap,
    get_store,
    safe_append,
)
from vernier.record.schema import EventKind

F = TypeVar("F", bound=Callable[..., Any])


def tool(func: F) -> F:
    """Decorator that records ToolCall / ToolResult / ToolError around ``func``."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        store = get_store()
        if store is None:
            return func(*args, **kwargs)

        seq = store.next_seq()
        try:
            payload = {
                "channel": "function",
                "name": func.__qualname__,
                "args_digest": digest(_jsonable_args(args, kwargs)),
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
                        "name": func.__qualname__,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                    logical_parent_seq=seq,
                )
            raise

        try:
            safe_append(
                store,
                seq=store.next_seq(),
                kind=EventKind.TOOL_RESULT,
                payload={
                    "channel": "function",
                    "name": func.__qualname__,
                    "result_digest": digest(_jsonable(result)),
                },
                logical_parent_seq=seq,
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
