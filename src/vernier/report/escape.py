"""Escape untrusted model/tool text for terminal-safe printing."""

from __future__ import annotations

import re
import unicodedata

# Cap display size so huge payloads cannot hang or flood the terminal.
MAX_ESCAPE_CHARS = 256 * 1024

# C0 controls except TAB/LF (includes CR), plus DEL, C1, bidi, zero-width.
_CONTROL_RE = re.compile(
    "["
    "\x00-\x08\x0b\x0c\x0e-\x1f\x7f"  # C0 minus TAB/LF; DEL (includes CR)
    "\x80-\x9f"  # C1
    "\u202a-\u202e"  # LRE/RLE/PDF/LRO/RLO
    "\u2066-\u2069"  # LRI/RLI/FSI/PDI
    "\ufeff"  # BOM / ZWNBSP
    "\u200b-\u200f"  # ZWSP / ZWNJ / ZWJ / LRM / RLM
    "]"
)

# CSI / OSC / DCS style ESC sequences commonly used for ANSI / OSC 8.
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|].*?(?:\x07|\x1b\\))")


def escape_untrusted(text: str) -> str:
    """Strip terminal control sequences and bidi overrides from ``text``.

    Model- and tool-supplied strings MUST pass through this before printing.
    Output is truncated to :data:`MAX_ESCAPE_CHARS`.
    """
    if not text:
        return ""
    # Truncate before regex work so 10+ MiB payloads cannot hang the terminal.
    if len(text) > MAX_ESCAPE_CHARS:
        text = text[:MAX_ESCAPE_CHARS]
    cleaned = unicodedata.normalize("NFC", text)
    cleaned = _ANSI_RE.sub("", cleaned)
    cleaned = _CONTROL_RE.sub("", cleaned)
    # Explicitly drop CR (carriage-return overwrite) if any remain.
    cleaned = cleaned.replace("\r", "")
    out: list[str] = []
    for ch in cleaned:
        cat = unicodedata.category(ch)
        if cat.startswith("C") and ch not in {"\t", "\n"}:
            continue
        out.append(ch)
    return "".join(out)
