"""Turning projected category differentials into a number a draft pick can be judged by.

A matchup is nine roughly-independent weekly contests, so the natural currency is
**probability of winning a category**, not a scalar aggregate of production. Two objectives
follow, and they are genuinely different problems:

* ``EACH_CATEGORY`` — maximise ``Σ_c P(win c)``. Every marginal bit of probability counts, so
  it rewards spreading effort to wherever wins are cheapest.
* ``MOST_CATEGORIES`` — maximise ``P(win ≥ ⌈C/2⌉)``. Margin beyond the majority is worthless,
  which is exactly what makes *conceding* categories rational: a 6-3 build beats a balanced
  5-4 one, and the optimizer discovers that without being told.

The majority probability is computed by a Poisson-binomial dynamic program in O(C²) rather
than by enumerating 2^(C-1) win/loss scenarios. It is the same quantity — exact under the
independence assumption, not an approximation of the enumeration — just cheaper, which matters
because this sits inside the innermost loop of the optimizer.

**Independence is assumed and is known to be false** (assumptions ledger A-DRAFT-3): this repo
has already measured category correlations. The independent form ships first because it is
what the published result validates; the correlated variant is a follow-up.
"""

from __future__ import annotations

import math

from fantasy_gm.config import CATEGORY_DIRECTION, PERCENTAGE_CATEGORIES
from fantasy_gm.draft.settings import DraftSettings, Objective

_SQRT2 = math.sqrt(2.0)


def _phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / _SQRT2))


def category_win_prob(
    mean_diff: float, var_diff: float, category: str, tie_margin: float = 0.0
) -> float:
    """P(the deciding team wins ``category``) under a normal approximation.

    ``mean_diff``/``var_diff`` are for (mine − opponent) in that category's natural units.
    Turnovers invert, since fewer is better.

    ``tie_margin`` applies a continuity correction for integer categories: a differential
    within ±margin counts as a tie, and a tie is scored as half a win. Defaults to 0, which
    treats ties as measure-zero — a simplification the ledger tracks.
    """
    direction = 1 if category in PERCENTAGE_CATEGORIES else CATEGORY_DIRECTION.get(category, 1)
    mu = direction * mean_diff
    sd = math.sqrt(max(var_diff, 1e-12))
    if tie_margin <= 0.0:
        return _phi(mu / sd)
    p_win = 1.0 - _phi((tie_margin - mu) / sd)
    p_loss = _phi((-tie_margin - mu) / sd)
    return p_win + 0.5 * max(0.0, 1.0 - p_win - p_loss)


def prob_at_least(probs: list[float], k: int) -> float:
    """P(at least ``k`` successes) for independent Bernoullis — Poisson-binomial DP."""
    if k <= 0:
        return 1.0
    if k > len(probs):
        return 0.0
    dist = [1.0]
    for p in probs:
        p = min(max(p, 0.0), 1.0)
        nxt = [0.0] * (len(dist) + 1)
        for wins, mass in enumerate(dist):
            nxt[wins] += mass * (1.0 - p)
            nxt[wins + 1] += mass * p
        dist = nxt
    return sum(dist[k:])


def score_objective(probs: list[float], settings: DraftSettings) -> float:
    """Collapse per-category win probabilities into the value being maximised."""
    if settings.objective is Objective.EACH_CATEGORY:
        return sum(probs)
    return prob_at_least(probs, settings.majority)


def category_probabilities(
    mean_diffs: dict[str, float],
    var_diffs: dict[str, float],
    settings: DraftSettings,
    tie_margin: float = 0.0,
) -> list[float]:
    return [
        category_win_prob(
            mean_diffs.get(c, 0.0), var_diffs.get(c, 1e-12), c, tie_margin
        )
        for c in settings.categories
    ]
