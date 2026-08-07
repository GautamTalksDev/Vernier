"""Wall-clock, random, and env-read capture / replay stubs."""

from __future__ import annotations

import os
import random
import time
from typing import Any

from vernier.capture.recorder import get_store, is_active, safe_append
from vernier.record.schema import EventKind
from vernier.replay.session import get_catalog, record_replay_miss

_ENV_NONDET = "VERNIER_CAPTURE_NONDET"

_installed = False
_real_time = time.time
_real_time_ns = time.time_ns
_real_monotonic = time.monotonic
_real_random = random.random
_real_getenv = os.getenv


def install_nondet_hooks() -> None:
    """Patch time/random/os.getenv for capture and replay."""
    global _installed
    if _installed:
        return
    _installed = True

    def time_time() -> float:
        catalog = get_catalog()
        if catalog is not None and catalog.had_wall_clock:
            value = catalog.take_time()
            if value is None:
                raise record_replay_miss(
                    channel="wall_clock",
                    detail="no recorded time.time value remaining",
                )
            _record_wall_clock(time=value)
            return value
        value = _real_time()
        if _should_record():
            _record_wall_clock(time=value)
        return value

    def time_time_ns() -> int:
        catalog = get_catalog()
        if catalog is not None and catalog.had_wall_clock:
            value = catalog.take_time_ns()
            if value is None:
                seconds = catalog.take_time()
                if seconds is None:
                    raise record_replay_miss(
                        channel="wall_clock",
                        detail="no recorded time.time_ns value remaining",
                    )
                value = int(seconds * 1_000_000_000)
            _record_wall_clock(time_ns=value)
            return value
        value = _real_time_ns()
        if _should_record():
            _record_wall_clock(time_ns=value)
        return value

    def time_monotonic() -> float:
        return time_time()

    def random_random() -> float:
        catalog = get_catalog()
        if catalog is not None and catalog.had_random:
            value = catalog.take_random()
            if value is None:
                raise record_replay_miss(
                    channel="random",
                    detail="no recorded random.random value remaining",
                )
            _record_random(value)
            return value
        value = _real_random()
        if _should_record():
            _record_random(value)
        return value

    def getenv(key: str, default: Any = None) -> Any:
        if key.startswith("VERNIER_"):
            return _real_getenv(key, default)
        catalog = get_catalog()
        if catalog is not None and key in catalog.env_keys:
            found, value = catalog.take_env(key)
            if not found:
                raise record_replay_miss(
                    channel="env",
                    detail=f"no recorded env read remaining for {key!r}",
                )
            _record_env(key, value)
            return default if value is None else value
        value = _real_getenv(key)
        if _should_record():
            _record_env(key, value)
        return default if value is None else value

    time.time = time_time
    time.time_ns = time_time_ns
    time.monotonic = time_monotonic
    random.random = random_random
    os.getenv = getenv


def uninstall_nondet_hooks() -> None:
    global _installed
    if not _installed:
        return
    time.time = _real_time
    time.time_ns = _real_time_ns
    time.monotonic = _real_monotonic
    random.random = _real_random
    os.getenv = _real_getenv
    _installed = False


def _should_record() -> bool:
    return is_active() and get_catalog() is None and os.environ.get(_ENV_NONDET) == "1"


def _record_wall_clock(
    *, time: float | None = None, time_ns: int | None = None
) -> None:
    store = get_store()
    if store is None:
        return
    payload: dict[str, Any] = {}
    if time is not None:
        payload["time"] = time
    if time_ns is not None:
        payload["time_ns"] = time_ns
    if not payload:
        return
    safe_append(
        store,
        seq=store.next_seq(),
        kind=EventKind.WALL_CLOCK,
        payload=payload,
    )


def _record_random(value: float) -> None:
    store = get_store()
    if store is None:
        return
    safe_append(
        store,
        seq=store.next_seq(),
        kind=EventKind.RANDOM,
        payload={"value": value},
    )


def _record_env(name: str, value: str | None) -> None:
    store = get_store()
    if store is None:
        return
    safe_append(
        store,
        seq=store.next_seq(),
        kind=EventKind.ENV_READ,
        payload={"name": name, "value": value},
    )
