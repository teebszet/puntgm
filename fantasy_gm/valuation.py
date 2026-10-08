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


def _player_games(
    store, season: str, as_of: str | None = None, played_only: bool = True,
) -> dict[str, list[dict]]:
    """Per-player stat lines for a season, optionally restricted to games known by ``as_of``.

    ``played_only`` (the default) drops rows with no recorded playing time — a DNP row
    carries signal only through the availability model, never through a per-game rate mean
    or pool eligibility (the board's D1 split). A row with no usage snapshot at all counts
    as played: minutes unknown, but the stat line is real.
    """
    sql = (
        "SELECT l.player_id, l.stats_json FROM player_logs l "
        "LEFT JOIN usage_role u ON u.player_id = l.player_id AND u.known_from = l.game_date "
        "WHERE l.season = ?"
    )
    args: list = [season]
    if as_of is not None:
        sql += " AND l.game_date <= ?"
        args.append(as_of)
    if played_only:
        sql += " AND (u.minutes IS NULL OR u.minutes > 0)"
    out: dict[str, list[dict]] = {}
    for r in store.conn.execute(sql, args):
        out.setdefault(r["player_id"], []).append(json.loads(r["stats_json"]))
    return out


# Depth-chart position -> implied minutes, for players a ranked season cannot place: a
# player with no NBA history, or whose ranked season contains no usable sample (a season
# lost to injury). The curve is a documented estimate, not a measurement — depth 1 projects
# to starter minutes, depth 15 to garbage minutes — and is recorded as derived provenance
# wherever it ranks someone.
DEPTH_MINUTES_TOP = 36.0
DEPTH_MINUTES_STEP = 2.0
DEPTH_MINUTES_FLOOR = 6.0


def depth_implied_minutes(depth_chart_pos: int) -> float:
    """The minutes a depth-chart position implies, for pool placement without a sample."""
    return max(DEPTH_MINUTES_TOP - DEPTH_MINUTES_STEP * (depth_chart_pos - 1),
               DEPTH_MINUTES_FLOOR)


def last_healthy_sample(
    store, ranked_season: str, pids: list[str], *, min_games: int = 10,
) -> dict[str, tuple[str, list[dict]]]:
    """Each player's most recent season *before* ``ranked_season`` with at least
    ``min_games`` games actually played (the D1 rule) — his last healthy baseline.

    A season lost to injury must zero-rate nobody: the pool places such a player by derived
    depth (:func:`rosterable_pool`), and pricing needs per-game rates, which a lost season
    does not carry. Walks seasons backwards and takes the first one each player clears the
    floor. Returns ``{player_id: (season, stat_lines)}``; a player no season prices is
    simply absent, which callers surface as unpriced rather than hiding.

    Rates are measured from complete seasons — the board's ``as_of`` discipline guards the
    availability fit against seeing the season being ranked, not the rate basis of seasons
    already finished.
    """
    if not pids:
        return {}
    want = set(pids)
    seasons = [r["season"] for r in store.conn.execute(
        "SELECT DISTINCT season FROM player_logs WHERE season < ? ORDER BY season DESC",
        (ranked_season,),
    )]
    out: dict[str, tuple[str, list[dict]]] = {}
    for season in seasons:
        games = _player_games(store, season)
        for pid in list(want):
            lines = games.get(pid, [])
            if len(lines) >= min_games:
                out[pid] = (season, lines)
                want.discard(pid)
        if not want:
            break
    return out


def rosterable_pool(
    store, season: str, pool_size: int = 156, min_games: int = 10,
    games: dict[str, list[dict]] | None = None, as_of: str | None = None,
    forward_season: str | None = None,
) -> list[str]:
    """The set of players a real league would roster, ranked by **minutes per game** — the
    true starter/role signal. Ranking by games played (the old approach) wrongly excludes
    stars who miss a handful of nights (Jokić at 65 games) while keeping durable role players,
    dumping the stars onto the wire. A light ``min_games`` floor keeps tiny samples out.
    Falls back to games played only if no usage/minutes data exists.

    The minutes are the **ranked season's** minutes per game over games actually played —
    ``usage_role`` rows inside that season's window, DNP rows (minutes 0) excluded — not a
    career average that lets an old role outrank the current one. Players the ranked season
    cannot place (below the ``min_games`` floor) are placed by their derived depth on the
    ``forward_season`` projected roster when one is given; without ``forward_season`` they
    stay out of the pool, as before.
    """
    games = games if games is not None else _player_games(store, season, as_of=as_of)
    eligible = [p for p in games if len(games[p]) >= min_games] or list(games)

    # Minutes must respect the same as-of gate as the box scores, or a point-in-time
    # valuation silently ranks players by a role they had not yet earned. The window is the
    # ranked season's, so an earlier season's role cannot outrank the current one.
    window = store.conn.execute(
        "SELECT MIN(game_date) lo, MAX(game_date) hi FROM games WHERE season = ?", (season,)
    ).fetchone()
    rank_min: dict[str, float] = {}
    if window and window["lo"] and window["hi"]:
        sql = (
            "SELECT player_id, AVG(minutes) m FROM usage_role "
            "WHERE known_from >= ? AND known_from <= ? AND minutes > 0"
        )
        args: list = [window["lo"], window["hi"]]
        if as_of is not None:
            sql += " AND known_from <= ?"
            args.append(as_of)
        sql += " GROUP BY player_id"
        rank_min = {r["player_id"]: r["m"]
                    for r in store.conn.execute(sql, args)}

    # Players without a usable ranked-season sample are ranked by derived depth on their
    # projected roster (R2's injury edge): a season lost to injury must not zero-rank the
    # player, and a rookie must not vanish from the pool he will be drafted from.
    placed: dict[str, float] = {}
    if forward_season:
        gate = as_of or "9999-12-31"
        for r in store.conn.execute(
            """SELECT fr.player_id, fr.depth_chart_pos
               FROM forward_roster fr
               JOIN (SELECT player_id, MAX(known_from) kf FROM forward_roster
                     WHERE season = ? AND known_from <= ? GROUP BY player_id) latest
                 ON latest.player_id = fr.player_id AND latest.kf = fr.known_from
               WHERE fr.season = ?""",
            (forward_season, gate, forward_season),
        ):
            pid = r["player_id"]
            if pid not in eligible:
                placed[pid] = depth_implied_minutes(r["depth_chart_pos"])

    eligible_set = set(eligible)

    def _rank_key(pid: str) -> tuple[float, int, str]:
        # A player's ranked-season minutes count when he is eligible at all (the floor
        # filter, or the tiny-store fallback that admits everyone); otherwise his placement
        # is derived depth, or the bottom if nothing places him.
        mins = rank_min.get(pid) if pid in eligible_set else None
        mins = mins if mins is not None else placed.get(pid, 0.0)
        return (-mins, -len(games.get(pid, ())), pid)

    if rank_min or placed:
        eligible_plus = eligible + [p for p in placed if p not in eligible_set]
        eligible_plus.sort(key=_rank_key)
        return eligible_plus[:pool_size]
    if not games:
        return []
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
