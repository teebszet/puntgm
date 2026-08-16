"""Data-derived player valuation (A6) — z-scores, not asserted weights.

Replaces the ad-hoc ``store._fantasy_points`` proxy (pts×1, reb×1.2, … stl×3, blk×3, …) with
the standard 9-cat z-score value: each counting category is standardised by the league mean/σ
over a rosterable player pool, so every category contributes equally in standardised units.
Percentage categories use the volume-weighted *impact* form — (player% − league%) × attempts —
then standardised, so a high-% low-volume shooter isn't overrated.

The z-score *is* the measured value; there is nothing asserted to tune. Baselines are computed
over the top ``pool_size`` players by games played (the rosterable universe), so deep-bench
scrubs don't distort the league σ.
"""

from __future__ import annotations

import json
from statistics import fmean, pstdev

from fantasy_gm.config import CATEGORY_DIRECTION, DEFAULT_CATEGORIES, PERCENTAGE_CATEGORIES


def _counting(categories: list[str]) -> list[str]:
    return [c for c in categories if c not in PERCENTAGE_CATEGORIES]


def _player_games(store, season: str, as_of: str | None = None) -> dict[str, list[dict]]:
    """Per-player stat lines for a season, optionally restricted to games known by ``as_of``."""
    if as_of is None:
        rows = store.conn.execute(
            "SELECT player_id, stats_json FROM player_logs WHERE season = ?", (season,)
        )
    else:
        rows = store.conn.execute(
            "SELECT player_id, stats_json FROM player_logs WHERE season = ? AND game_date <= ?",
            (season, as_of),
        )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["player_id"], []).append(json.loads(r["stats_json"]))
    return out


def rosterable_pool(
    store, season: str, pool_size: int = 156, min_games: int = 10,
    games: dict[str, list[dict]] | None = None, as_of: str | None = None,
) -> list[str]:
    """The set of players a real league would roster, ranked by **minutes per game** — the
    true starter/role signal. Ranking by games played (the old approach) wrongly excludes
    stars who miss a handful of nights (Jokić at 65 games) while keeping durable role players,
    dumping the stars onto the wire. A light ``min_games`` floor keeps tiny samples out.
    Falls back to games played only if no usage/minutes data exists.
    """
    games = games if games is not None else _player_games(store, season, as_of=as_of)
    eligible = [p for p in games if len(games[p]) >= min_games] or list(games)
    # Minutes must respect the same as-of gate as the box scores, or a point-in-time
    # valuation silently ranks players by a role they had not yet earned.
    if as_of is None:
        rows = store.conn.execute(
            "SELECT player_id, AVG(minutes) m FROM usage_role GROUP BY player_id")
    else:
        rows = store.conn.execute(
            "SELECT player_id, AVG(minutes) m FROM usage_role WHERE known_from <= ? "
            "GROUP BY player_id", (as_of,))
    mins = {r["player_id"]: r["m"] for r in rows}
    if mins:
        eligible.sort(key=lambda p: (-(mins.get(p) or 0.0), -len(games[p]), p))
    else:
        eligible.sort(key=lambda p: (-len(games[p]), p))
    return eligible[:pool_size]


_VALUE_CACHE: dict[tuple, dict[str, float]] = {}


def player_values(
    store, season: str, pool_size: int = 156, categories: list[str] | None = None,
    as_of: str | None = None,
) -> dict[str, float]:
    """Return {player_id: total 9-cat z-value} for the rosterable pool (top ``pool_size`` by
    minutes per game). Higher is better; turnovers count negatively.

    Memoized per (store, season, pool_size): a season's z-values are constant, and hot loops
    (reconcile, wire, season replay) call this repeatedly on a static store. Call
    ``clear_value_cache()`` if the store's player data changes underneath a long-lived process.

    Pass ``as_of`` for a **point-in-time** valuation computed only from games played on or
    before that date. Anything simulating in-season behaviour must use it: full-season
    z-values are hindsight, and an opponent with hindsight holds exactly the players who
    turn out well, which distorts the league more than never moving at all."""
    n_logs = store.conn.execute(
        "SELECT COUNT(*) c FROM player_logs WHERE season = ?", (season,)
    ).fetchone()["c"]
    key = (id(store), season, pool_size, n_logs, as_of)  # row count guards against id() reuse
    cached = _VALUE_CACHE.get(key)
    if cached is not None:
        return cached
    categories = categories or list(DEFAULT_CATEGORIES)
    counting = _counting(categories)
    pcts = [c for c in categories if c in PERCENTAGE_CATEGORIES]
    games = _player_games(store, season, as_of=as_of)
    if not games:
        return {}
    pool = rosterable_pool(store, season, pool_size=pool_size, games=games, as_of=as_of)

    # per-player season aggregates over the pool
    agg: dict[str, dict[str, float]] = {}
    for pid in pool:
        gs = games[pid]
        rec: dict[str, float] = {c: fmean([g.get(c, 0.0) for g in gs]) for c in counting}
        for c in pcts:
            mk, at = PERCENTAGE_CATEGORIES[c]
            made, att = sum(g.get(mk, 0.0) for g in gs), sum(g.get(at, 0.0) for g in gs)
            rec[f"{c}_pct"] = made / att if att > 0 else 0.0
            rec[f"{c}_att"] = fmean([g.get(at, 0.0) for g in gs])
        agg[pid] = rec

    # league baselines over the pool
    base: dict[str, tuple[float, float]] = {}
    for c in counting:
        vals = [agg[p][c] for p in pool]
        base[c] = (fmean(vals), pstdev(vals) or 1.0)
    impact_base: dict[str, tuple[float, float, float]] = {}
    for c in pcts:
        mk, at = PERCENTAGE_CATEGORIES[c]
        tot_made = sum(sum(g.get(mk, 0.0) for g in games[p]) for p in pool)
        tot_att = sum(sum(g.get(at, 0.0) for g in games[p]) for p in pool)
        league_pct = tot_made / tot_att if tot_att > 0 else 0.0
        impacts = [(agg[p][f"{c}_pct"] - league_pct) * agg[p][f"{c}_att"] for p in pool]
        impact_base[c] = (league_pct, fmean(impacts), pstdev(impacts) or 1.0)

    values: dict[str, float] = {}
    for pid in pool:
        z = 0.0
        for c in counting:
            mean, std = base[c]
            z += CATEGORY_DIRECTION[c] * (agg[pid][c] - mean) / std
        for c in pcts:
            league_pct, mean_imp, std_imp = impact_base[c]
            imp = (agg[pid][f"{c}_pct"] - league_pct) * agg[pid][f"{c}_att"]
            z += (imp - mean_imp) / std_imp
        values[pid] = round(z, 4)
    _VALUE_CACHE[key] = values
    return values


_RATE_CACHE: dict[tuple, dict[str, float]] = {}


def league_percentage_rates(store, season: str, pool_size: int = 156) -> dict[str, float]:
    """Pool-wide Σmakes/Σattempts per percentage category — the replacement level a
    shooter's contribution is measured against.

    Shared by valuation (the z-score impact term) and the engine's candidate ranking, so
    both judge a shooter the same way: what matters is ``(rate − league_rate) × attempts``,
    not the rate alone. A player who went 3-for-3 has a spectacular rate and contributes
    essentially nothing.
    """
    # Cached on the store itself, not in a module dict keyed by a row count: this sits in
    # the engine's candidate-ranking hot loop (~400 wire players per contested category),
    # and a COUNT(*) over the season's logs per lookup made a season replay ~10x slower.
    # upsert_player_logs invalidates it, which is the only thing that can change the rates.
    cached = getattr(store, "_pct_rate_cache", {}).get((season, pool_size))
    if cached is not None:
        return cached
    games = _player_games(store, season)
    pool = rosterable_pool(store, season, pool_size=pool_size, games=games)
    out: dict[str, float] = {}
    for c, (mk, at) in PERCENTAGE_CATEGORIES.items():
        made = sum(g.get(mk, 0.0) for p in pool for g in games[p])
        att = sum(g.get(at, 0.0) for p in pool for g in games[p])
        out[c] = made / att if att > 0 else 0.0
    if not hasattr(store, "_pct_rate_cache"):
        store._pct_rate_cache = {}
    store._pct_rate_cache[(season, pool_size)] = out
    return out


def clear_value_cache() -> None:
    """Drop the memoized z-values (call if a store's player data changed mid-process)."""
    _RATE_CACHE.clear()
    _VALUE_CACHE.clear()
