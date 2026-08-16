"""Exact max-reward assignment of players to roster slots (design D4).

Greedy slot-filling silently mis-prices multi-eligible players — precisely the ones worth
paying up for, because their value is that they keep future options open. A centre who can
only fill C is worth less than an identical player who fills C and PF, and only an exact
assignment sees that.

This is the Jonker-Volgenant / Kuhn-Munkres shortest-augmenting-path algorithm, O(n³). It is
implemented here rather than pulled from scipy because scipy is otherwise not a dependency of
this package and a 13×13 assignment does not justify one. Correctness is pinned against a
brute-force permutation search on small matrices in the tests.

Ineligibility is expressed as ``-inf`` reward; the solver maps it to a large finite penalty so
an infeasible roster still returns *an* assignment, which the caller can then detect and
report rather than crashing (spec: "an unfillable roster structure is reported").
"""

from __future__ import annotations

import math

# Large enough to dominate any real reward, small enough to keep arithmetic finite.
_INELIGIBLE = 1e9


def solve_assignment(reward: list[list[float]]) -> tuple[list[int], float]:
    """Assign rows to columns maximising total reward.

    ``reward[r][c]`` may be ``-inf`` for a forbidden pairing. Returns ``(assignment, total)``
    where ``assignment[r]`` is the column for row ``r`` (or ``-1`` if unassigned because there
    are more rows than columns), and ``total`` sums only the finite rewards actually used.

    Rectangular input is handled: the shorter side is padded internally with zero-reward
    dummies, so more players than slots (or vice versa) is fine.
    """
    n_rows = len(reward)
    n_cols = len(reward[0]) if n_rows else 0
    if n_rows == 0 or n_cols == 0:
        return ([-1] * n_rows, 0.0)

    n = max(n_rows, n_cols)
    # Minimise cost = -reward. Pad with zeros so the square problem is always feasible.
    cost = [[0.0] * n for _ in range(n)]
    for r in range(n_rows):
        for c in range(n_cols):
            v = reward[r][c]
            cost[r][c] = _INELIGIBLE if v == -math.inf else -float(v)

    # --- shortest augmenting path (JV) ---------------------------------------
    INF = float("inf")
    u = [0.0] * (n + 1)   # row potentials
    v = [0.0] * (n + 1)   # column potentials
    p = [0] * (n + 1)     # p[col] = row matched to col (1-indexed; 0 = free)
    way = [0] * (n + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j], way[j] = cur, j0
                if minv[j] < delta:
                    delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0], j0 = p[j1], j1

    assignment = [-1] * n_rows
    total = 0.0
    for col in range(1, n + 1):
        row = p[col] - 1
        if 0 <= row < n_rows and col - 1 < n_cols:
            val = reward[row][col - 1]
            if val != -math.inf:
                assignment[row] = col - 1
                total += float(val)
    return assignment, total


def assign_to_slots(
    player_positions: dict[str, frozenset[str]],
    players: list[str],
    slots,
    rewards: dict[str, float] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Fit ``players`` into ``slots``.

    Returns ``({player_id: slot_name}, unplaced_player_ids)``. ``unplaced`` being non-empty is
    how an unfillable roster structure surfaces — the caller reports it rather than the solver
    guessing. ``rewards`` optionally prefers placing more valuable players when slots are
    scarce; by default every legal placement is equally good, so the solver is only deciding
    feasibility.
    """
    if not players or not slots:
        return ({}, list(players))
    rewards = rewards or {}
    matrix = [
        [
            rewards.get(pid, 1.0)
            if slot.accepts(player_positions.get(pid, frozenset()))
            else -math.inf
            for slot in slots
        ]
        for pid in players
    ]
    assignment, _ = solve_assignment(matrix)
    placed: dict[str, str] = {}
    unplaced: list[str] = []
    for i, pid in enumerate(players):
        col = assignment[i]
        if col < 0:
            unplaced.append(pid)
        else:
            placed[pid] = slots[col].name
    return placed, unplaced
