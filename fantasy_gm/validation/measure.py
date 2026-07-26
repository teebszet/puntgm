"""Measured parameters that replace asserted constants.

* ``measure_category_cv`` (A1): per-category coefficient of variation (σ/μ), league-median.
  This is the direct test of "which categories are higher variance" — measured, not asserted.
* ``derive_variance_profile`` (A1→A2): normalise measured CVs to a median of 1.0 — a *descriptive*
  relative-variance summary. It is NOT fed to the projector: real data showed the projector's
  measured per-player σ already captures category volatility (games are ~independent, A4), so a
  multiplier would double-count. Kept for reporting only.
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


def measure_autocorrelation(
    store, season: str, categories: list[str] | None = None, min_games: int = 20
) -> dict[str, float]:
    """Median lag-1 autocorrelation of per-game production per counting category (A4).

    ~0 means games are independent, so Var(k-game sum) ≈ k·Var(single game) and the
    projector's Σ rg·σ² is correct with **no** category variance multiplier — the measured
    per-player σ already carries the spread. Positive values mean multi-game variance is
    under-counted (a correction could be justified); negative means over-counted.
    """
    import json

    categories = categories or list(DEFAULT_CATEGORIES)
    counting = _counting(categories)
    per_cat: dict[str, list[float]] = {c: [] for c in counting}
    rows = store.conn.execute(
        """SELECT player_id, stats_json FROM player_logs
           WHERE season = ? ORDER BY player_id, game_date""",
        (season,),
    )
    seqs: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        stats = json.loads(r["stats_json"])
        d = seqs.setdefault(r["player_id"], {c: [] for c in counting})
        for c in counting:
            d[c].append(stats.get(c, 0.0))
    for d in seqs.values():
        for c in counting:
            x = d[c]
            if len(x) < min_games:
                continue
            mean = statistics.fmean(x)
            var = statistics.pvariance(x)
            if var <= 0:
                continue
            num = sum((x[i] - mean) * (x[i + 1] - mean) for i in range(len(x) - 1))
            per_cat[c].append(num / ((len(x) - 1) * var))
    return {c: statistics.median(v) for c, v in per_cat.items() if v}


def measure_category_correlations(
    store, season: str, categories: list[str] | None = None, pool_size: int = 156
) -> dict[str, dict[str, float]]:
    """Pearson correlation of per-player per-category production across the rosterable pool (A9).

    Reveals the positional *bundles*: which categories co-occur in players and which trade off,
    so "availability of a category" is really "which bundle is available, and at what cost" — e.g.
    adding a PG for assists also brings steals/3PM/FT% and concedes rebounds/blocks/FG%.
    """
    import json

    categories = categories or list(DEFAULT_CATEGORIES)
    games: dict[str, list[dict]] = {}
    for r in store.conn.execute(
        "SELECT player_id, stats_json FROM player_logs WHERE season = ?", (season,)
    ):
        games.setdefault(r["player_id"], []).append(json.loads(r["stats_json"]))
    pool = sorted(games, key=lambda p: -len(games[p]))[:pool_size]

    vec: dict[str, list[float]] = {c: [] for c in categories}
    for pid in pool:
        gs = games[pid]
        for c in categories:
            if c in PERCENTAGE_CATEGORIES:
                mk, at = PERCENTAGE_CATEGORIES[c]
                made, att = sum(g.get(mk, 0.0) for g in gs), sum(g.get(at, 0.0) for g in gs)
                vec[c].append(made / att if att > 0 else 0.0)
            else:
                vec[c].append(statistics.fmean([g.get(c, 0.0) for g in gs]))

    def _corr(a: list[float], b: list[float]) -> float:
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        sa, sb = statistics.pstdev(a), statistics.pstdev(b)
        if sa == 0 or sb == 0:
            return 0.0
        return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=False)) / (len(a) * sa * sb)

    return {c: {d: round(_corr(vec[c], vec[d]), 3) for d in categories} for c in categories}


def bootstrap_category_winprob(
    store, my_players: list[str], opp_players: list[str], category: str,
    period_start: str, as_of: str, period_end: str, n: int = 1000, seed: int = 0,
    window: int | None = None,
) -> float:
    """Monte-Carlo win probability for a counting ``category``: resample each player's real
    per-game production over their remaining games and compare team totals. The empirical
    check for the projector's normal approximation (A3). ``window`` limits the sampled games
    to the last N (match the projector's ``recent_games_window`` for a fair comparison)."""
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
            logs = store.player_logs_asof(as_of, player_id=pid)
            if window is not None:
                logs = logs[-window:]
            vals = [lg.stats.get(category, 0.0) for lg in logs]
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


def bootstrap_pct_winprob(
    store, my_players: list[str], opp_players: list[str], category: str,
    period_start: str, as_of: str, period_end: str, n: int = 1000, seed: int = 0,
    window: int | None = None,
) -> float:
    """Monte-Carlo win probability for a **percentage** category (A12): resample each player's
    real per-game (makes, attempts) pairs over remaining games and compare volume-weighted
    Σmakes/Σattempts. The empirical check for the projector's binomial standard error.
    ``window`` limits sampled games to the last N (match the projector's window)."""
    mk, at = PERCENTAGE_CATEGORIES[category]
    rng = random.Random(seed)

    def _side(players):
        banked = store.category_totals(players, period_start, as_of, [mk, at])
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
            logs = store.player_logs_asof(as_of, player_id=pid)
            if window is not None:
                logs = logs[-window:]
            pairs = [(lg.stats.get(mk, 0.0), lg.stats.get(at, 0.0)) for lg in logs]
            if pairs:
                draws.append((rg, pairs))
        return banked[mk], banked[at], draws

    def _pct(banked_mk, banked_at, draws):
        m, a = banked_mk, banked_at
        for rg, pairs in draws:
            for _ in range(rg):
                pm, pa = rng.choice(pairs)
                m += pm
                a += pa
        return (m / a) if a > 0 else 0.0

    my = _side(my_players)
    opp = _side(opp_players)
    wins = 0.0
    for _ in range(n):
        d = _pct(*my) - _pct(*opp)  # higher percentage is better
        wins += 1.0 if d > 0 else (0.5 if d == 0 else 0.0)
    return wins / n
