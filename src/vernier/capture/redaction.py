"""Header / URL / body redaction for transport capture.

Sensitive header *names* are omitted entirely. Query parameters and JSON
object fields whose names look like secrets are replaced with a fixed
sentinel so record files never retain canary material (verified by grep).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"

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
        "openai-organization",
        "anthropic-version",
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

# Any header name matching this pattern is dropped (case-insensitive).
_BLOCKED_NAME_RE = re.compile(r"token|secret|key|auth", re.IGNORECASE)

# JSON / query field names (exact, case-insensitive) whose values are redacted.
_SECRET_FIELD_NAMES = frozenset(
    {
        "key",
        "token",
        "secret",
        "password",
        "api_key",
        "api-key",
        "apikey",
        "access_token",
        "refresh_token",
        "client_secret",
    }
)


def _is_blocked(name: str) -> bool:
    lowered = name.lower().strip()
    if lowered in _BLOCKED_EXACT:
        return True
    if any(part in lowered for part in _BLOCKED_SUBSTRINGS):
        return True
    return _BLOCKED_NAME_RE.search(lowered) is not None


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of headers safe to persist (blocked keys omitted)."""
    return {k: v for k, v in headers.items() if not _is_blocked(k)}


def _is_secret_field(name: str) -> bool:
    return name.lower().strip() in _SECRET_FIELD_NAMES


def redact_url(url: str) -> str:
    """Redact secret-looking query parameter values in ``url``."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if _is_secret_field(key) or _BLOCKED_NAME_RE.search(key):
            pairs.append((key, REDACTED))
        else:
            pairs.append((key, value))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment)
    )


def redact_json_value(obj: Any) -> Any:
    """Deep-copy JSON-like data, redacting secret-named object fields."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and _is_secret_field(key):
                out[key] = REDACTED
            else:
                out[key] = redact_json_value(value)
        return out
    if isinstance(obj, list):
        return [redact_json_value(item) for item in obj]
    if isinstance(obj, tuple):
        return [redact_json_value(item) for item in obj]
    return obj


def redact_body_bytes(body: bytes) -> bytes:
    """If ``body`` is JSON, return redacted JSON bytes; otherwise return as-is."""
    if not body:
        return body
    try:
        text = body.decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    redacted = redact_json_value(data)
    return json.dumps(redacted, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
