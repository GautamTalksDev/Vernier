"""Needleman–Wunsch global sequence alignment with deterministic tie-breaks.

Tie-breaking
------------
When two or more predecessor scores at cell ``(i, j)`` are equal to the
best score, the predecessor is chosen in this **fixed** priority order:

1. **diagonal** (from ``i-1, j-1``) — consumes source index ``i-1``
2. **up / delete** (from ``i-1, j``) — consumes source index ``i-1``
3. **left / insert** (from ``i, j-1``) — does not consume a source index

This prefers alignments that consume the **lower source (A) index** as soon
as an equal score allows. Priority is encoded as integer constants; the
algorithm never iterates dict keys for control flow.
"""

from __future__ import annotations

from enum import IntEnum

from vernier.align.costs import AlignCosts, load_costs
from vernier.align.types import AlignedPair, AlignEvent, Relation


class AlignmentTooLongError(ValueError):
    """Raised when either sequence exceeds the configured max length."""


class _Move(IntEnum):
    """Predecessor choice; numeric order is the tie-break priority."""

    DIAG = 0
    UP = 1
    LEFT = 2
    STOP = 3


def align(
    a: list[AlignEvent] | tuple[AlignEvent, ...],
    b: list[AlignEvent] | tuple[AlignEvent, ...],
    *,
    costs: AlignCosts | None = None,
) -> tuple[AlignedPair, ...]:
    """Globally align ``a`` (source) with ``b`` (target).

    Returns an ordered tuple of :class:`AlignedPair` from start to end.
    """
    if costs is None:
        costs = load_costs()

    n = len(a)
    m = len(b)
    if n > costs.max_events or m > costs.max_events:
        raise AlignmentTooLongError(
            f"refusing to align runs of length {n} and {m}; "
            f"max_events={costs.max_events}"
        )

    # score[i][j] = best score aligning a[:i] with b[:j]
    # move[i][j]  = chosen predecessor (tie-broken)
    neg_inf = -10**18
    score = [[0] * (m + 1) for _ in range(n + 1)]
    move: list[list[_Move]] = [[_Move.STOP] * (m + 1) for _ in range(n + 1)]

    gap = costs.gap
    for i in range(1, n + 1):
        score[i][0] = i * gap
        move[i][0] = _Move.UP
    for j in range(1, m + 1):
        score[0][j] = j * gap
        move[0][j] = _Move.LEFT

    for i in range(1, n + 1):
        ai = a[i - 1]
        for j in range(1, m + 1):
            bj = b[j - 1]
            diag_score, _is_match = costs.score_diagonal(ai, bj)
            candidates: list[tuple[int, _Move]] = [
                (score[i - 1][j - 1] + diag_score, _Move.DIAG),
                (score[i - 1][j] + gap, _Move.UP),
                (score[i][j - 1] + gap, _Move.LEFT),
            ]
            # Deterministic tie-break: highest score, then lowest _Move value
            # (DIAG < UP < LEFT) so lower source index is preferred.
            best_score = neg_inf
            best_move = _Move.DIAG
            for cand_score, cand_move in candidates:
                if cand_score > best_score or (
                    cand_score == best_score and cand_move < best_move
                ):
                    best_score = cand_score
                    best_move = cand_move
            score[i][j] = best_score
            move[i][j] = best_move

    # Traceback
    pairs_rev: list[AlignedPair] = []
    i, j = n, m
    while i > 0 or j > 0:
        muv = move[i][j]
        if muv is _Move.DIAG:
            ai = a[i - 1]
            bj = b[j - 1]
            _s, is_match = costs.score_diagonal(ai, bj)
            relation = Relation.MATCHED if is_match else Relation.SUBSTITUTED
            pairs_rev.append(AlignedPair(i - 1, j - 1, relation))
            i -= 1
            j -= 1
        elif muv is _Move.UP:
            pairs_rev.append(AlignedPair(i - 1, None, Relation.DELETED))
            i -= 1
        elif muv is _Move.LEFT:
            pairs_rev.append(AlignedPair(None, j - 1, Relation.INSERTED))
            j -= 1
        else:
            raise RuntimeError(f"invalid move at ({i},{j})")

    pairs_rev.reverse()
    return tuple(pairs_rev)
