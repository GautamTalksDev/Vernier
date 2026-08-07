"""Zero-edit capture bootstrap installed via sitecustomize / import hooks."""

from __future__ import annotations

import atexit
import builtins
import contextlib
import sys
from typing import Any

from vernier.capture.recorder import activate_from_env, deactivate
from vernier.capture.transport import install_httpx_hooks, install_requests_hooks

_installed = False
_hooking = False
_real_import = builtins.__import__


def install() -> None:
    """Activate the recorder and patch HTTP clients as they are imported."""
    global _installed
    if _installed:
        return
    _installed = True

    store = activate_from_env()
    if store is None:
        return

    if "httpx" in sys.modules:
        install_httpx_hooks()
    if "requests" in sys.modules:
        install_requests_hooks()

    builtins.__import__ = _patched_import  # type: ignore[assignment]
    atexit.register(_shutdown)


def _patched_import(
    name: str,
    globals: dict[str, Any] | None = None,
    locals: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> Any:
    global _hooking
    module = _real_import(name, globals, locals, fromlist, level)
    if _hooking:
        return module
    _hooking = True
    try:
        root = name.split(".", 1)[0]
        if root == "httpx":
            install_httpx_hooks()
        elif root == "requests":
            install_requests_hooks()
    except Exception:  # noqa: BLE001 — never break imports
        pass
    finally:
        _hooking = False
    return module


def _shutdown() -> None:
    global _installed
    with contextlib.suppress(Exception):
        deactivate()
    builtins.__import__ = _real_import
    _installed = False


def reset_for_tests() -> None:
    """Restore import hook state between unit tests."""
    _shutdown()
