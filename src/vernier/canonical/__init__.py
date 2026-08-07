"""Canonicalization and hashing (pure, no I/O).

``canonicalize`` turns any JSON-compatible value into exactly one
deterministic byte sequence. Downstream alignment and classification are
worthless if this is subtly wrong.

Normalization order (fixed, do not reorder):

1. **Size gate** — reject any single string over 8 MiB *before* transforms,
   so hostile input cannot force large NFC work or JCS buffers.
2. **Unicode NFC** — same abstract characters must hash the same whether
   the producer emitted precomposed or decomposed form.
3. **Line endings** — CRLF/CR -> LF after NFC so combining marks are already
   composed before we rewrite U+000D sequences; ending policy is content,
   not platform accident.
4. **No control/ZWSP stripping** — zero-width and bidi overrides in tool
   arguments are signal; erasing them creates false matches.
5. **RFC 8785 JCS** — lexicographic keys by UTF-16 code unit, no
   insignificant whitespace, ECMAScript number serialization.
6. **Version prefix** — ``vernier-canon-v1\\n`` so a future format change
   cannot collide with v1 digests.
7. **SHA-256** — digest of the *prefixed* bytes as ``sha256:<hex>``.
"""

from __future__ import annotations

import hashlib
from typing import Any

from vernier.canonical._jcs import serialize_jcs
from vernier.canonical._normalize import normalize_tree, reject_oversized
from vernier.canonical.errors import CanonicalError, JcsError, ValueTooLargeError

CANON_VERSION_TAG = b"vernier-canon-v1\n"

__all__ = [
    "CANON_VERSION_TAG",
    "CanonicalError",
    "JcsError",
    "ValueTooLargeError",
    "canonicalize",
    "digest",
]


def canonicalize(obj: Any) -> bytes:
    """Return the version-prefixed canonical byte sequence for ``obj``."""
    reject_oversized(obj)
    normalized = normalize_tree(obj)
    body = serialize_jcs(normalized)
    return CANON_VERSION_TAG + body


def digest(obj: Any) -> str:
    """Return ``sha256:<hex>`` over :func:`canonicalize` output."""
    digest_hex = hashlib.sha256(canonicalize(obj)).hexdigest()
    return f"sha256:{digest_hex}"
