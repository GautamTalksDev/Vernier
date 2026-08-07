"""Escape untrusted model/tool text for terminal-safe printing."""

from __future__ import annotations

import re
import unicodedata

# C0 controls except TAB/LF, plus DEL, C1 controls, bidi overrides, zero-width.
_CONTROL_RE = re.compile(
    "["
    "\x00-\x08\x0b\x0c\x0e-\x1f\x7f"  # C0 minus TAB/LF; DEL
    "\x80-\x9f"  # C1
    "\u202a-\u202e"  # LRE/RLE/PDF/LRO/RLO
    "\u2066-\u2069"  # LRI/RLI/FSI/PDI
    "\ufeff"  # BOM / ZWNBSP
    "\u200b-\u200f"  # ZWSP / ZWNJ / ZWJ / LRM / RLM
    "]"
)

# CSI / OSC / DCS style ESC sequences commonly used for ANSI.
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|].*?(?:\x07|\x1b\\))")


def escape_untrusted(text: str) -> str:
    """Strip terminal control sequences and bidi overrides from ``text``.

    Model- and tool-supplied strings MUST pass through this before printing.
    """
    if not text:
        return ""
    # Normalize to NFC so combining tricks are less surprising, then strip.
    cleaned = unicodedata.normalize("NFC", text)
    cleaned = _ANSI_RE.sub("", cleaned)
    cleaned = _CONTROL_RE.sub("", cleaned)
    # Replace remaining non-printables (category C*) except space separators.
    out: list[str] = []
    for ch in cleaned:
        cat = unicodedata.category(ch)
        if cat.startswith("C") and ch not in {"\t", "\n"}:
            continue
        out.append(ch)
    return "".join(out)
