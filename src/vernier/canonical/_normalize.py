"""Pre-JCS normalization: size gate, NFC, line endings.

Order is intentional and fixed — see ``canonicalize`` module docs.
"""

from __future__ import annotations

from typing import Any
from unicodedata import normalize

from vernier.canonical.errors import CanonicalError, ValueTooLargeError

# 8 MiB
MAX_VALUE_BYTES = 8 * 1024 * 1024
# Hard nesting limit for tool args / JSON trees (stack-overflow defense).
MAX_JSON_DEPTH = 256


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def reject_oversized(obj: Any, _depth: int = 0) -> None:
    """Rule 1: reject any single string value over 8 MiB; cap nesting depth."""
    if _depth > MAX_JSON_DEPTH:
        raise ValueError(
            f"JSON nesting depth exceeds limit ({MAX_JSON_DEPTH}); "
            "refusing deeply nested input"
        )
    if isinstance(obj, str):
        size = _utf8_size(obj)
        if size > MAX_VALUE_BYTES:
            raise ValueTooLargeError(size, MAX_VALUE_BYTES)
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise CanonicalError(
                    f"object keys must be str, got {type(key).__name__}"
                )
            reject_oversized(key, _depth + 1)
            reject_oversized(value, _depth + 1)
        return
    if isinstance(obj, (list, tuple)):
        for item in obj:
            reject_oversized(item, _depth + 1)


def normalize_string(value: str) -> str:
    """Apply rules 2–4 to one string.

    2. Unicode NFC
    3. CRLF -> LF, then CR -> LF
    4. Preserve zero-width, bidi overrides, and other controls (no stripping)
    """
    value = normalize("NFC", value)
    # CRLF first so a lone CR is not left behind from a pair.
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value


def normalize_tree(obj: Any, _depth: int = 0) -> Any:
    """Deep-copy ``obj`` applying string normalization; structure preserved."""
    if _depth > MAX_JSON_DEPTH:
        raise ValueError(
            f"JSON nesting depth exceeds limit ({MAX_JSON_DEPTH}); "
            "refusing deeply nested input"
        )
    if isinstance(obj, str):
        out = normalize_string(obj)
        size = _utf8_size(out)
        if size > MAX_VALUE_BYTES:
            raise ValueTooLargeError(size, MAX_VALUE_BYTES)
        return out
    if isinstance(obj, dict):
        return {
            normalize_string(key) if isinstance(key, str) else key: normalize_tree(
                value, _depth + 1
            )
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [normalize_tree(item, _depth + 1) for item in obj]
    if isinstance(obj, tuple):
        return [normalize_tree(item, _depth + 1) for item in obj]
    return obj
