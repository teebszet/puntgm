"""Seeded simulated-league generation — the primary source of league state (D7).

Given a backfilled season, build a plausible league: draft rosters from an ADP ordering
(proxied by season production), snake-draft across N teams, and lay down a weekly
round-robin matchup schedule. Everything is reproducible from ``seed`` + settings, and
rosters are dated from the draft (season start) so point-in-time reads hold.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from fantasy_gm.config import CADENCE_WEEKLY, DEFAULT_CATEGORIES
from fantasy_gm.models import Matchup


def _season_bounds(store, season: str) -> tuple[date, date]:
    row = store.conn.execute(
        "SELECT MIN(game_date) AS lo, MAX(game_date) AS hi FROM games WHERE season = ?",
        (season,),
    ).fetchone()
    if not row or row["lo"] is None:
        raise ValueError(f"no games for season {season!r}; backfill first")
    return date.fromisoformat(row["lo"]), date.fromisoformat(row["hi"])


def _adp_order(store, season: str) -> list[str]:
    """Player ids ordered by total season production (a stand-in for ADP)."""
    rows = store.conn.execute(
        "SELECT player_id, stats_json FROM player_logs WHERE season = ?", (season,)
    )
    import json

    totals: dict[str, float] = {}
    for r in rows:
        totals[r["player_id"]] = totals.get(r["player_id"], 0.0) + store.fantasy_points(
            json.loads(r["stats_json"])
        )
    return [pid for pid, _ in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))]


def simulate_league(
    store,
    season: str,
    seed: int,
    n_teams: int = 8,
    roster_size: int = 10,
    cadence: str = CADENCE_WEEKLY,
    categories: list[str] | None = None,
    league_id: str | None = None,
) -> str:
    """Create a reproducible simulated league. Returns the league_id.

    Same ``seed`` + settings => identical rosters and matchup schedule.
    """
    categories = categories or list(DEFAULT_CATEGORIES)
    league_id = league_id or f"sim-{season}-{seed}-{n_teams}x{roster_size}"
    rng = random.Random(seed)

    lo, _hi = _season_bounds(store, season)
    draft_date = lo.isoformat()  # rosters are known from the (pre-season-proxy) draft

    store.create_league(league_id, f"Simulated {season} (#{seed})", season, cadence,
                        categories, is_real=False, seed=seed)
    team_ids = [f"T{i:02d}" for i in range(n_teams)]
    for tid in team_ids:
        store.add_team(league_id, tid, f"Team {tid}")

    # --- snake draft from ADP, with a small seed-driven "reach" for realism --
    pool = _adp_order(store, season)
    # apply bounded reach noise deterministically from the seed
    reachable = pool[: n_teams * roster_size * 2] or pool
    ordered: list[str] = []
    remaining = list(reachable)
    while remaining and len(ordered) < len(reachable):
        window = remaining[: min(3, len(remaining))]  # reach up to 2 spots
        pick = rng.choice(window)
        ordered.append(pick)
        remaining.remove(pick)
    ordered += [p for p in pool if p not in set(ordered)]

    idx = 0
    for rnd in range(roster_size):
        order = team_ids if rnd % 2 == 0 else list(reversed(team_ids))
        for tid in order:
            if idx >= len(ordered):
                break
            store.add_roster_event(league_id, tid, ordered[idx], "add", draft_date)
            idx += 1

    # --- weekly round-robin matchup schedule (circle method) -----------------
    _schedule_matchups(store, league_id, team_ids, lo, _hi, rng)
    return league_id


def _schedule_matchups(store, league_id, team_ids, lo, hi, rng) -> None:
    teams = list(team_ids)
    if len(teams) % 2 == 1:
        teams.append("BYE")
    n = len(teams)
    # week boundaries aligned to the Monday of the first game week
    first_monday = lo - timedelta(days=lo.weekday())
    period = 0
    wk = first_monday
    while wk <= hi:
        period_start = wk.isoformat()
        period_end = (wk + timedelta(days=6)).isoformat()
        # circle-method pairing rotated by period
        rot = teams[:1] + teams[1:][period % (n - 1):] + teams[1:][: period % (n - 1)]
        for a, b in zip(rot[: n // 2], rot[n - 1: n // 2 - 1: -1]):
            if "BYE" in (a, b):
                continue
            store.add_matchup(Matchup(league_id, period, period_start, period_end, a, b))
        period += 1
        wk = wk + timedelta(days=7)
