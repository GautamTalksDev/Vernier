"""RFC 8785 JSON Canonicalization Scheme (JCS) serializer.

Number serialization follows ECMA-262 §7.1.12.1 (incl. Note 2), adapted from
Anders Rundgren's reference implementation (Apache-2.0). See NOTICE.
"""

from __future__ import annotations

import math
import re
from typing import Any

from vernier.canonical.errors import JcsError

# Safe integer domain for IEEE 754 binary64 (I-JSON / ECMAScript).
_INT_MAX = 2**53 - 1
_INT_MIN = -(2**53) + 1

_ESCAPE = re.compile(r'[\x00-\x1f\\"\b\f\n\r\t]')
_ESCAPE_DCT: dict[str, str] = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}
for _i in range(0x20):
    _ESCAPE_DCT.setdefault(chr(_i), f"\\u{_i:04x}")


def _replace_escape(match: re.Match[str]) -> str:
    return _ESCAPE_DCT[match.group(0)]


def _serialize_str(value: str, parts: list[bytes]) -> None:
    parts.append(b'"')
    try:
        parts.append(_ESCAPE.sub(_replace_escape, value).encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise JcsError("string contains non-UTF-8 code points") from exc
    parts.append(b'"')


def _serialize_float(value: float, parts: list[bytes]) -> None:
    if math.isnan(value) or math.isinf(value):
        raise JcsError("NaN and Infinity are not permitted in JCS")

    # +0 and -0 both serialize as 0 (JSON.stringify / JCS).
    if value == 0.0:
        parts.append(b"0")
        return

    if value < 0.0:
        parts.append(b"-")
        _serialize_float(-value, parts)
        return

    # Adapted from Anders Rundgren's reference JCS implementation.
    stringified = str(value)

    exponent_str = ""
    exponent_value = 0
    q = stringified.find("e")
    if q > 0:
        exponent_str = stringified[q:]
        if len(exponent_str) > 2 and exponent_str[2:3] == "0":
            exponent_str = exponent_str[:2] + exponent_str[3:]
        stringified = stringified[:q]
        exponent_value = int(exponent_str[1:])

    first = stringified
    dot = ""
    last = ""
    q = stringified.find(".")
    if q > 0:
        dot = "."
        first = stringified[:q]
        last = stringified[q + 1 :]

    if last == "0":
        dot = ""
        last = ""

    if 0 < exponent_value < 21:
        first += last
        last = ""
        dot = ""
        exponent_str = ""
        q = exponent_value - len(first)
        while q >= 0:
            q -= 1
            first += "0"
    elif -7 < exponent_value < 0:
        last = first + last
        first = "0"
        dot = "."
        exponent_str = ""
        q = exponent_value
        while q < -1:
            q += 1
            last = "0" + last

    parts.append(f"{first}{dot}{last}{exponent_str}".encode())


def serialize_jcs(obj: Any) -> bytes:
    """Serialize ``obj`` to RFC 8785 JCS UTF-8 bytes (no version prefix)."""
    parts: list[bytes] = []
    _serialize(obj, parts)
    return b"".join(parts)


def _serialize(obj: Any, parts: list[bytes]) -> None:
    if obj is None:
        parts.append(b"null")
        return
    if isinstance(obj, bool):
        parts.append(b"true" if obj else b"false")
        return
    if isinstance(obj, int):
        # Coerce subclasses (e.g. IntEnum) back to plain int.
        number = int(obj)
        if number < _INT_MIN or number > _INT_MAX:
            raise JcsError(f"{number} exceeds safe integer domain for JSON floats")
        parts.append(str(number).encode("ascii"))
        return
    if isinstance(obj, float):
        _serialize_float(obj, parts)
        return
    if isinstance(obj, str):
        _serialize_str(obj, parts)
        return
    if isinstance(obj, (list, tuple)):
        parts.append(b"[")
        for index, item in enumerate(obj):
            if index:
                parts.append(b",")
            _serialize(item, parts)
        parts.append(b"]")
        return
    if isinstance(obj, dict):
        # Lexicographic order by UTF-16 code units (big-endian code unit bytes).
        try:
            items = sorted(obj.items(), key=lambda kv: kv[0].encode("utf-16-be"))
        except AttributeError as exc:
            raise JcsError("object keys must be strings") from exc
        parts.append(b"{")
        for index, (key, value) in enumerate(items):
            if index:
                parts.append(b",")
            if not isinstance(key, str):
                raise JcsError(f"object keys must be str, got {type(key).__name__}")
            _serialize_str(key, parts)
            parts.append(b":")
            _serialize(value, parts)
        parts.append(b"}")
        return
    raise JcsError(f"unsupported type for JCS: {type(obj).__name__}")
