"""Baseline opponent management — give every simulated team a competent-but-dumb manager.

Simulated leagues draft once and freeze. By midseason that is badly unrealistic: nobody
cuts a bust, nobody streams, injured players are held all year, and so the waiver wire stays
stocked with players a real league would have claimed in October. That inflates every
wire-based claim the replay makes — the engine is picking from a pool that would not exist.

This module walks the season and lets each team make a small number of moves per scoring
period, so the wire drains the way a real one does.

Two properties matter more than the manager being smart:

* **Point-in-time.** The manager may only use what was knowable on the decision date, so it
  values players with ``player_values(..., as_of=…)`` — the same 9-cat z-value the draft
  used, restricted to games already played. Full-season z-values would be hindsight, and an
  opponent with hindsight holds exactly the players who turn out well, which distorts the
  league more than never moving at all.
* **Deterministic.** Same league and settings produce the same moves, so replays stay
  reproducible. Teams act in a rotating order per period rather than always in team-id
  order, so ``T00`` doesn't get first pick on the wire every single week.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from fantasy_gm.config import Config


@dataclass
class ManagementReport:
    periods: int
    moves: int
    teams: int
    skipped_no_candidate: int


def _asof_value(store, pid: str, values: dict[str, float], as_of: str, pwin: int) -> float:
    """Point-in-time 9-cat z-value, scaled by participation.

    An earlier version used the ``fantasy_points`` proxy on the theory that a simulated
    opponent should be crude. It was crude in a specifically wrong way: that proxy ignores
    FG%/FT% entirely (the A6 finding), so the manager cheerfully dropped efficient guards
    for counting-stat bigs and *added* good players back to the wire — the opposite of what
    this module exists to do. In the 12-team leagues, whose 156 roster spots exactly consume
    the rosterable pool, it pushed top-156 players free from ~1 back up to ~48.

    Using the same z-value the draft used keeps rosters coherent; ``as_of`` keeps it honest.
    Players outside the point-in-time pool score below everyone in it.
    """
    z = values.get(pid)
    if z is None:
        return float("-inf")
    q = store.participation_rate(pid, as_of, window=pwin)
    return z * (1.0 if q is None else q) if z > 0 else z


def apply_baseline_management(
    store,
    league_id: str,
    config: Config | None = None,
    moves_per_period: int = 1,
    offset_days: int = 1,
    force: bool = False,
) -> ManagementReport:
    """Let every team stream over the course of the season, writing roster events.

    Run **once**, after ``simulate_league``. Re-running is not idempotent — the second pass
    sees post-management rosters and layers further moves on top — so it refuses unless
    ``force=True``. To regenerate, re-simulate the league (which clears its events) first.
    """
    config = config or Config()
    meta = store.league_meta(league_id)
    if meta is None:
        raise KeyError(f"unknown league {league_id!r}")
    season = meta["season"]
    pwin = config.participation_window

    periods = store.conn.execute(
        """SELECT DISTINCT period_index, period_start, period_end FROM matchups
           WHERE league_id = ? ORDER BY period_index""",
        (league_id,),
    ).fetchall()
    if not periods:
        return ManagementReport(0, 0, 0, 0)

    # The draft date is the earliest roster event, NOT the earliest period start: the
    # matchup schedule is aligned to the Monday preceding the season opener, so period 0
    # can begin before anyone was drafted.
    draft_row = store.conn.execute(
        "SELECT MIN(known_from) d FROM roster_events WHERE league_id = ?", (league_id,)
    ).fetchone()
    draft_date = draft_row["d"] if draft_row and draft_row["d"] else None
    already = store.conn.execute(
        "SELECT COUNT(*) n FROM roster_events WHERE league_id = ? AND known_from > ?",
        (league_id, draft_date or ""),
    ).fetchone()["n"]
    if already and not force:
        raise RuntimeError(
            f"{league_id} already has {already} post-draft roster events; management is not "
            "idempotent. Re-simulate the league to regenerate, or pass force=True."
        )

    teams = store.team_ids(league_id)
    moves = skipped = 0

    for p in periods:
        as_of = (date.fromisoformat(p["period_start"])
                 + timedelta(days=offset_days)).isoformat()
        if as_of > p["period_end"]:
            continue
        # Every team at this as-of faces the same player pool, so score it once for the
        # whole period rather than once per team — the naive version re-scored ~580
        # players per team per week and dominated the runtime.
        scores = _score_pool(store, season, as_of, p["period_end"], pwin)

        # rotate who acts first so the same team doesn't always win the wire
        order = teams[p["period_index"] % len(teams):] + teams[:p["period_index"] % len(teams)]
        for team in order:
            for _ in range(moves_per_period):
                if _make_one_move(store, league_id, team, as_of, scores):
                    moves += 1
                else:
                    skipped += 1

    return ManagementReport(len(periods), moves, len(teams), skipped)


def _score_pool(store, season, as_of, period_end, pwin) -> dict[str, tuple[int, float]]:
    """``{player_id: (remaining_games, as-of value)}`` for everyone known at ``as_of``."""
    from fantasy_gm.valuation import player_values

    values = player_values(store, season, as_of=as_of)
    rg_by_team: dict[str, int] = {}
    out: dict[str, tuple[int, float]] = {}
    for pid, _name, _t in store.player_universe(season, as_of):
        nba_team = store.player_team(pid, as_of)
        if nba_team is None:
            continue
        if nba_team not in rg_by_team:
            rg_by_team[nba_team] = store.remaining_games_for_team(nba_team, as_of, period_end)
        out[pid] = (rg_by_team[nba_team], _asof_value(store, pid, values, as_of, pwin))
    return out


def _make_one_move(store, league_id, team, as_of, scores) -> bool:
    """Drop the least useful rostered player and add the best available, if that helps."""
    state = store.league_state_asof(league_id, as_of)
    roster = list(state.rosters.get(team, []))
    if not roster:
        return False
    rostered = state.rostered_player_ids()

    # worst rostered player: no games left first, then lowest trailing value
    scored = sorted(
        ((scores.get(pid, (0, float("-inf")))[0], scores.get(pid, (0, float("-inf")))[1], pid)
         for pid in roster),
        key=lambda x: (x[0] > 0, x[1]),
    )
    drop_rg, drop_val, drop_id = scored[0]

    best = None
    for pid, (rg, val) in scores.items():
        if pid in rostered or rg <= 0 or val == float("-inf"):
            continue
        if best is None or val > best[0]:
            best = (val, pid)
    if best is None:
        return False

    add_val, add_id = best
    # only move if the wire actually improves on what we'd cut
    if drop_rg > 0 and add_val <= drop_val:
        return False

    store.add_roster_event(league_id, team, drop_id, "drop", as_of)
    store.add_roster_event(league_id, team, add_id, "add", as_of)
    return True
