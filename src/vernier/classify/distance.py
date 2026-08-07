"""Word-level Levenshtein distance (D11/D12 only)."""

from __future__ import annotations


def tokenize_words(text: str) -> list[str]:
    """Unicode whitespace tokenization (``str.split()`` with no args)."""
    return text.split()


def word_edit_distance(a: str, b: str) -> int:
    """Levenshtein distance on word tokens; insert/delete/substitute cost 1."""
    ta = tokenize_words(a)
    tb = tokenize_words(b)
    n, m = len(ta), len(tb)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    cur = [0] * (m + 1)
    for i in range(1, n + 1):
        cur[0] = i
        for j in range(1, m + 1):
            cost = 0 if ta[i - 1] == tb[j - 1] else 1
            cur[j] = min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + cost,
            )
        prev, cur = cur, prev
    return prev[m]


def word_distance_ratio(a: str, b: str) -> float:
    """``d / max(len(ta), len(tb))``; 0.0 when both empty."""
    ta = tokenize_words(a)
    tb = tokenize_words(b)
    n = max(len(ta), len(tb))
    if n == 0:
        return 0.0
    return word_edit_distance(a, b) / n


WORD_DISTANCE_THRESHOLD = 0.30
