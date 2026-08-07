"""Header redaction for transport capture.

Sensitive header *names* are omitted entirely so record files never contain
``Authorization``, ``api-key``, or ``cookie`` (verified by grep in tests).
"""

from __future__ import annotations

from collections.abc import Mapping

# Exact names (case-insensitive) that must never appear in records.
_BLOCKED_EXACT = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "proxy-authorization",
        "api-key",
        "x-api-key",
        "x-api_key",
    }
)

# Substrings; if present in a normalized header name, drop the header.
_BLOCKED_SUBSTRINGS = (
    "authorization",
    "api-key",
    "apikey",
    "api_key",
    "cookie",
)


def _is_blocked(name: str) -> bool:
    lowered = name.lower().strip()
    if lowered in _BLOCKED_EXACT:
        return True
    return any(part in lowered for part in _BLOCKED_SUBSTRINGS)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of headers safe to persist (blocked keys omitted)."""
    return {k: v for k, v in headers.items() if not _is_blocked(k)}
