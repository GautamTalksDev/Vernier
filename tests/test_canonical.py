"""Table-driven tests for deterministic canonicalization."""

from __future__ import annotations

import unicodedata
from typing import Any

import pytest

from vernier.canonical import (
    CANON_VERSION_TAG,
    CanonicalError,
    JcsError,
    ValueTooLargeError,
    canonicalize,
    digest,
)
from vernier.canonical._jcs import serialize_jcs
from vernier.canonical._normalize import MAX_VALUE_BYTES


def test_version_prefix_present() -> None:
    out = canonicalize({"a": 1})
    assert out.startswith(CANON_VERSION_TAG)
    assert out == CANON_VERSION_TAG + b'{"a":1}'


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(
            {"msg": "hello\r\nworld"},
            {"msg": "hello\nworld"},
            id="crlf-vs-lf",
        ),
        pytest.param(
            {"msg": "hello\rworld"},
            {"msg": "hello\nworld"},
            id="cr-vs-lf",
        ),
    ],
)
def test_line_endings_identical_digest(left: Any, right: Any) -> None:
    assert digest(left) == digest(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(
            {"city": "café"},
            {"city": unicodedata.normalize("NFD", "café")},
            id="nfc-vs-nfd-cafe",
        ),
        pytest.param(
            {"x": "Å"},
            {"x": "A\u030a"},
            id="nfc-vs-nfd-angstrom",
        ),
    ],
)
def test_unicode_nfc_identical_digest(left: Any, right: Any) -> None:
    assert left != right  # raw Python values differ
    assert digest(left) == digest(right)


def test_key_order_identical_digest() -> None:
    a = {"b": 1, "a": 2, "c": 3}
    b = {"c": 3, "a": 2, "b": 1}
    assert list(a.keys()) != list(b.keys())
    assert digest(a) == digest(b)
    assert canonicalize(a) == CANON_VERSION_TAG + b'{"a":2,"b":1,"c":3}'


def test_one_point_zero_vs_one_identical_per_jcs() -> None:
    """JCS uses IEEE 754 numbers: 1 and 1.0 are the same value -> ``1``."""
    as_int = {"n": 1}
    as_float = {"n": 1.0}
    assert digest(as_int) == digest(as_float)
    assert serialize_jcs(as_float) == b'{"n":1}'


def test_zero_width_space_changes_digest() -> None:
    without = {"arg": "hello"}
    with_zwsp = {"arg": "hel\u200blo"}
    assert digest(without) != digest(with_zwsp)


def test_nested_five_levels() -> None:
    nested: Any = {"v": 1}
    for depth in range(4):
        nested = {"child": nested, "level": depth}
    # Depth: outer + 4 wraps = 5 object levels.
    out = canonicalize(nested)
    assert out.startswith(CANON_VERSION_TAG)
    # Stable across rebuild
    again: Any = {"v": 1}
    for depth in range(4):
        again = {"level": depth, "child": again}
    assert digest(nested) == digest(again)


def test_empty_string_null_missing_key_are_distinct() -> None:
    empty_string = {"a": ""}
    null_value = {"a": None}
    missing_key: dict[str, Any] = {}
    digests = {
        digest(empty_string),
        digest(null_value),
        digest(missing_key),
    }
    assert len(digests) == 3


def test_nine_mib_value_errors() -> None:
    huge = "x" * (9 * 1024 * 1024)
    assert len(huge.encode("utf-8")) > MAX_VALUE_BYTES
    with pytest.raises(ValueTooLargeError, match="8 MiB") as excinfo:
        digest({"blob": huge})
    assert excinfo.value.size == 9 * 1024 * 1024
    assert excinfo.value.limit == MAX_VALUE_BYTES


@pytest.mark.parametrize(
    ("value", "jcs_body"),
    [
        (None, b"null"),
        (True, b"true"),
        (False, b"false"),
        ([], b"[]"),
        ({}, b"{}"),
        ([1, "a", None], b'[1,"a",null]'),
        (0.0, b"0"),
        (-0.0, b"0"),
        (1e-6, b"0.000001"),
        (1e20, b"100000000000000000000"),
        (1e21, b"1e+21"),
    ],
)
def test_jcs_primitives(value: Any, jcs_body: bytes) -> None:
    assert serialize_jcs(value) == jcs_body
    assert canonicalize(value) == CANON_VERSION_TAG + jcs_body


def test_digest_format() -> None:
    d = digest({"ok": True})
    assert d.startswith("sha256:")
    hexpart = d.removeprefix("sha256:")
    assert len(hexpart) == 64
    int(hexpart, 16)


def test_unsupported_type() -> None:
    with pytest.raises(JcsError, match="unsupported type"):
        canonicalize({"x": b"bytes"})


def test_non_string_key() -> None:
    with pytest.raises(CanonicalError):
        canonicalize({1: "bad"})  # type: ignore[dict-item]


def test_nan_rejected() -> None:
    with pytest.raises(JcsError, match="NaN"):
        canonicalize(float("nan"))


def test_infinity_rejected() -> None:
    with pytest.raises(JcsError, match="Infinity"):
        canonicalize(float("inf"))


def test_unsafe_integer_rejected() -> None:
    with pytest.raises(JcsError, match="safe integer"):
        canonicalize(2**53)


def test_utf16_key_order_non_ascii() -> None:
    # From RFC 8785 §3.2.3 sample family: euro before emoji by UTF-16 units.
    obj = {
        "\U0001f600": 1,  # grinning face (surrogate pair in UTF-16)
        "\u20ac": 2,  # euro sign
        "a": 3,
    }
    body = serialize_jcs(obj)
    # 'a' (0x0061), euro (0x20ac), emoji high surrogate 0xd83d...
    assert body == b'{"a":3,"\xe2\x82\xac":2,"\xf0\x9f\x98\x80":1}'


def test_control_chars_preserved_in_strings() -> None:
    # Rule 4: not stripped; JSON-escaped in JCS output.
    assert serialize_jcs("a\nb") == b'"a\\nb"'
    assert serialize_jcs("\u0001") == b'"\\u0001"'


def test_negative_float_and_tuple() -> None:
    assert serialize_jcs(-1.5) == b"-1.5"
    assert digest(("a", 1)) == digest(["a", 1])


def test_normalize_tree_rejects_post_transform_oversize() -> None:
    from vernier.canonical._normalize import normalize_tree

    with pytest.raises(ValueTooLargeError):
        normalize_tree("y" * (MAX_VALUE_BYTES + 1))


def test_jcs_non_string_key_paths() -> None:
    with pytest.raises(JcsError, match="object keys must be str"):
        serialize_jcs({1: "x"})  # type: ignore[dict-item]

    class WeirdKey:
        def encode(self, _encoding: str) -> bytes:
            return b"\x00a"

    with pytest.raises(JcsError, match="object keys must be str"):
        serialize_jcs({WeirdKey(): 1})  # type: ignore[dict-item]


def test_lone_surrogate_rejected() -> None:
    with pytest.raises(JcsError, match="non-UTF-8"):
        serialize_jcs("\ud800")
