"""Measured parameters that replace asserted constants.

* ``measure_category_cv`` (A1): per-category coefficient of variation (σ/μ), league-median.
  This is the direct test of "which categories are higher variance" — measured, not asserted.
* ``derive_variance_profile`` (A1→A2): turn measured CVs into per-category variance multipliers
  the ``Projector`` consumes, normalised so the median category = 1.0. If the measured profile
  reproduces observed volatility, the hand-set multiplier is redundant.
* ``bootstrap_category_winprob`` (A3): Monte-Carlo end-of-period win probability by resampling
  each player's real per-game lines over remaining games — the ground truth to check the
  projector's normal approximation against (it is weakest for low-count cats like blocks/steals).
"""

from __future__ import annotations

import random
import statistics

from fantasy_gm.config import (
    CATEGORY_DIRECTION,
    DEFAULT_CATEGORIES,
    PERCENTAGE_CATEGORIES,
)

_FAR_FUTURE = "9999-12-31"  # post-hoc: validation sees the whole (already-played) season


def _counting(categories: list[str]) -> list[str]:
    return [c for c in categories if c not in PERCENTAGE_CATEGORIES]


def measure_category_cv(
    store, season: str, categories: list[str] | None = None, min_games: int = 5
) -> dict[str, float]:
    """Median coefficient of variation (σ/μ) per counting category across players with at
    least ``min_games`` games. Higher CV ⇒ higher relative game-to-game variance."""
    categories = categories or list(DEFAULT_CATEGORIES)
    counting = _counting(categories)
    per_cat: dict[str, list[float]] = {c: [] for c in counting}
    for pid, _name, _team in store.player_universe(season):
        logs = [lg for lg in store.player_logs_asof(_FAR_FUTURE, player_id=pid)
                if lg.season == season]
        if len(logs) < min_games:
            continue
        for c in counting:
            vals = [lg.stats.get(c, 0.0) for lg in logs]
            mu = statistics.fmean(vals)
            if mu > 0:
                per_cat[c].append(statistics.pstdev(vals) / mu)
    return {c: statistics.median(v) for c, v in per_cat.items() if v}


def derive_variance_profile(cv: dict[str, float]) -> dict[str, float]:
    """Normalise measured CVs into variance multipliers (median category → 1.0)."""
    if not cv:
        return {}
    med = statistics.median(cv.values())
    if med <= 0:
        return {c: 1.0 for c in cv}
    return {c: round(v / med, 3) for c, v in cv.items()}


def bootstrap_category_winprob(
    store, my_players: list[str], opp_players: list[str], category: str,
    period_start: str, as_of: str, period_end: str, n: int = 1000, seed: int = 0,
) -> float:
    """Monte-Carlo win probability for a counting ``category``: resample each player's real
    per-game production over their remaining games and compare team totals. The empirical
    check for the projector's normal approximation (A3)."""
    rng = random.Random(seed)
    direction = CATEGORY_DIRECTION[category]

    def _side(players):
        banked = store.category_totals(players, period_start, as_of, [category])[category]
        draws = []
        for pid in players:
            avail = store.availability_asof(pid, as_of)
            if avail and avail.status == "OUT":
                continue
            nba_team = store.player_team(pid, as_of)
            if not nba_team:
                continue
            rg = store.remaining_games_for_team(nba_team, as_of, period_end)
            if rg == 0:
                continue
            vals = [lg.stats.get(category, 0.0)
                    for lg in store.player_logs_asof(as_of, player_id=pid)]
            if vals:
                draws.append((rg, vals))
        return banked, draws

    my_banked, my_draws = _side(my_players)
    opp_banked, opp_draws = _side(opp_players)

    wins = 0.0
    for _ in range(n):
        my_tot = my_banked + sum(sum(rng.choice(v) for _ in range(rg)) for rg, v in my_draws)
        opp_tot = opp_banked + sum(sum(rng.choice(v) for _ in range(rg)) for rg, v in opp_draws)
        d = direction * (my_tot - opp_tot)
        wins += 1.0 if d > 0 else (0.5 if d == 0 else 0.0)
    return wins / n
