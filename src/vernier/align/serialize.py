"""Byte-stable serialization of alignment results."""

from __future__ import annotations

from vernier.align.types import AlignedPair, AlignEvent
from vernier.canonical._jcs import serialize_jcs


def event_to_dict(event: AlignEvent) -> dict[str, object]:
    out: dict[str, object] = {"kind": event.kind.value}
    if event.tool_name is not None:
        out["tool_name"] = event.tool_name
    return out


def pair_to_dict(pair: AlignedPair) -> dict[str, object]:
    return {
        "a_idx": pair.a_idx,
        "b_idx": pair.b_idx,
        "relation": pair.relation.value,
    }


def serialize_alignment(pairs: tuple[AlignedPair, ...] | list[AlignedPair]) -> bytes:
    """Serialize alignment as RFC 8785 JCS (UTF-8, no whitespace)."""
    payload = [pair_to_dict(p) for p in pairs]
    return serialize_jcs(payload)


def serialize_events(events: tuple[AlignEvent, ...] | list[AlignEvent]) -> bytes:
    return serialize_jcs([event_to_dict(e) for e in events])
