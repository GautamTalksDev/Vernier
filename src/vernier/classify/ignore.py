"""Ignore-list loading and application (divergence-v1)."""

from __future__ import annotations

import copy
import re
import tomllib
from pathlib import Path
from typing import Any

from vernier.classify.types import AppliedIgnore, IgnoreConfig

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,9})?"
    r"(?:Z|[+-]\d{2}:\d{2})$"
)

_IGNORE_SENTINEL: dict[str, bool] = {"$vernier_ignore": True}
_DEFAULT_PATH = Path(__file__).with_name("ignore-v1.toml")


def load_ignore_config(
    path: Path | None = None,
    *,
    overlay: Path | None = None,
) -> IgnoreConfig:
    """Load packaged defaults, optionally merged with ``overlay`` (vernier.toml)."""
    cfg = _parse_file(path or _DEFAULT_PATH)
    if overlay is not None and overlay.exists():
        over = _parse_file(overlay)
        cfg = IgnoreConfig(
            version=over.version,
            pointers=list(over.pointers),
            strip_uuid_values=over.strip_uuid_values,
            strip_iso_timestamps=over.strip_iso_timestamps,
        )
    return cfg


def _parse_file(path: Path) -> IgnoreConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    div = data.get("divergence", {})
    ign = div.get("ignore", {})
    return IgnoreConfig(
        version=str(div.get("version", "divergence-v1")),
        pointers=[str(p) for p in ign.get("pointers", [])],
        strip_uuid_values=bool(ign.get("strip_uuid_values", True)),
        strip_iso_timestamps=bool(ign.get("strip_iso_timestamps", True)),
    )


def apply_ignores(
    arguments: dict[str, Any] | None,
    config: IgnoreConfig,
    *,
    side: str,
) -> tuple[dict[str, Any] | None, list[AppliedIgnore]]:
    """Return (scrubbed_arguments, applied_ignores) for one side."""
    if arguments is None:
        return None, []
    tree: Any = copy.deepcopy(arguments)
    applied: list[AppliedIgnore] = []

    for pointer in config.pointers:
        if _pointer_exists(tree, pointer):
            _pointer_set(tree, pointer, dict(_IGNORE_SENTINEL))
            applied.append(
                AppliedIgnore(mechanism="pointer", pointer=pointer, side=side)
            )

    if config.strip_uuid_values or config.strip_iso_timestamps:
        _strip_shapes(
            tree,
            config,
            path="",
            side=side,
            applied=applied,
        )
    return tree, applied


def merge_applied_ignores(
    a_ignores: list[AppliedIgnore],
    b_ignores: list[AppliedIgnore],
) -> list[AppliedIgnore]:
    """Merge per-side ignores into both/a/b records, sorted stably."""
    key_to_sides: dict[tuple[str, str], set[str]] = {}
    for item in a_ignores:
        key_to_sides.setdefault((item.mechanism, item.pointer), set()).add("a")
    for item in b_ignores:
        key_to_sides.setdefault((item.mechanism, item.pointer), set()).add("b")
    out: list[AppliedIgnore] = []
    for mechanism, pointer in sorted(key_to_sides):
        sides = key_to_sides[(mechanism, pointer)]
        if sides == {"a", "b"}:
            side = "both"
        elif "a" in sides:
            side = "a"
        else:
            side = "b"
        out.append(AppliedIgnore(mechanism=mechanism, pointer=pointer, side=side))
    out.sort(key=lambda x: (x.mechanism, x.pointer, x.side))
    return out


def _strip_shapes(
    node: Any,
    config: IgnoreConfig,
    *,
    path: str,
    side: str,
    applied: list[AppliedIgnore],
) -> None:
    if isinstance(node, dict):
        if set(node.keys()) == {"$vernier_ignore"}:
            return
        for key in list(node.keys()):
            child_path = f"{path}/{_escape(key)}"
            val = node[key]
            if isinstance(val, str):
                mech = _shape_mechanism(val, config)
                if mech:
                    node[key] = dict(_IGNORE_SENTINEL)
                    applied.append(
                        AppliedIgnore(mechanism=mech, pointer=child_path, side=side)
                    )
            else:
                _strip_shapes(val, config, path=child_path, side=side, applied=applied)
    elif isinstance(node, list):
        for index, val in enumerate(node):
            child_path = f"{path}/{index}"
            if isinstance(val, str):
                mech = _shape_mechanism(val, config)
                if mech:
                    node[index] = dict(_IGNORE_SENTINEL)
                    applied.append(
                        AppliedIgnore(mechanism=mech, pointer=child_path, side=side)
                    )
            else:
                _strip_shapes(val, config, path=child_path, side=side, applied=applied)


def _shape_mechanism(value: str, config: IgnoreConfig) -> str | None:
    if config.strip_uuid_values and _UUID_RE.match(value):
        return "uuid"
    if config.strip_iso_timestamps and _ISO_RE.match(value):
        return "iso_timestamp"
    return None


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _pointer_exists(doc: Any, pointer: str) -> bool:
    try:
        _pointer_get(doc, pointer)
        return True
    except (KeyError, ValueError, IndexError, TypeError):
        return False


def _pointer_get(doc: Any, pointer: str) -> Any:
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    cur: Any = doc
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            if part not in cur:
                raise KeyError(pointer)
            cur = cur[part]
        else:
            raise KeyError(pointer)
    return cur


def _pointer_set(doc: Any, pointer: str, value: Any) -> None:
    if not pointer.startswith("/") or pointer == "/":
        raise ValueError(f"unsupported pointer for set: {pointer!r}")
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")]
    cur: Any = doc
    for part in parts[:-1]:
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value
