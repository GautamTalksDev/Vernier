"""Canonical body digests for captured HTTP payloads."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from vernier.canonical import CANON_VERSION_TAG, digest
from vernier.canonical._normalize import MAX_VALUE_BYTES

TRUNCATION_MARKER = "body_exceeds_8mib"


def digest_raw(data: bytes) -> str:
    """SHA-256 of version-prefixed raw bytes as ``sha256:<hex>``."""
    digest_hex = hashlib.sha256(CANON_VERSION_TAG + data).hexdigest()
    return f"sha256:{digest_hex}"


def body_digest(data: bytes) -> str:
    """Digest a body: JSON via :func:`digest`, else UTF-8 string / raw bytes."""
    if not data:
        return digest("")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return digest_raw(data)
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        return digest(text)
    return digest(parsed)


def check_body_size(data: bytes) -> tuple[bool, int]:
    """Return ``(exceeds_limit, size)``."""
    size = len(data)
    return size > MAX_VALUE_BYTES, size
