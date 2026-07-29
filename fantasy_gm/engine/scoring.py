"""Replay scoring (D8) — the track record.

Because replay is a fully-observed world, a suggested move is graded by what *actually*
happened: apply the added and dropped players' real production over the rest of the period
and check whether the move flipped a contested category or widened a margin, versus standing
pat. Projections are graded for calibration — did categories labeled "safe" actually hold.

These functions read *actual* box scores (not as-of), which is legitimate here: they run
after the fact, to score a call that was made point-in-time.
"""

from __future__ import annotations

from dataclasses import dataclass

from fantasy_gm.config import CATEGORY_DIRECTION, Config
from fantasy_gm.models import MatchupProjection, ReconciliationMove


@dataclass
class MoveGrade:
    flipped_to_me: list[str]      # categories the move flips from loss/tie to a win
    flipped_away: list[str]       # categories the move loses that stand-pat would win
    target_category: str
    target_realized_delta: float  # direction-adjusted change in the targeted category
    net_categories: int           # flipped_to_me minus flipped_away
    helped: bool


def _wins(mine: float, opp: float, cat: str) -> bool:
    return CATEGORY_DIRECTION[cat] * (mine - opp) > 0


def grade_move(store, move: ReconciliationMove, config: Config | None = None) -> MoveGrade:
    config = config or Config()
    cats = config.categories
    p = move.perspective
    matchup = store.matchup_by_period(p.league_id, p.period_index)
    if matchup is None:
        raise KeyError("no matchup for period")
    start, end, as_of = matchup.period_start, matchup.period_end, move.as_of
    state = store.league_state_asof(p.league_id, as_of)
    my_roster = list(state.rosters.get(p.team_id, []))
    opp_roster = list(state.rosters.get(p.opponent_team_id, []))

    # actual production already banked this period (all my players, incl. the drop)
    banked = store.category_totals(my_roster, start, as_of, cats)
    # remaining actual production (games after as_of) under each line
    rest_standpat = store.category_totals(my_roster, _next(as_of), end, cats)
    move_roster = [x for x in my_roster if x != move.drop_id] + [move.add_id]
    rest_move = store.category_totals(move_roster, _next(as_of), end, cats)
    opp_total = store.category_totals(opp_roster, start, end, cats)

    flipped_to_me, flipped_away = [], []
    for c in cats:
        standpat = banked[c] + rest_standpat[c]
        moved = banked[c] + rest_move[c]
        win_sp = _wins(standpat, opp_total[c], c)
        win_mv = _wins(moved, opp_total[c], c)
        if win_mv and not win_sp:
            flipped_to_me.append(c)
        elif win_sp and not win_mv:
            flipped_away.append(c)

    target = move.line_of_play.split()[-1].lower() if move.line_of_play else ""
    target = target if target in cats else (move.line_of_play or "")
    tdelta = 0.0
    if target in cats:
        tdelta = CATEGORY_DIRECTION[target] * (rest_move[target] - rest_standpat[target])
    net = len(flipped_to_me) - len(flipped_away)
    return MoveGrade(flipped_to_me, flipped_away, target, round(tdelta, 2), net, net > 0)


def calibration(store, projection: MatchupProjection, config: Config | None = None) -> dict:
    """Did categories the projection called 'safe' actually hold? Returns hit counts."""
    config = config or Config()
    cats = config.categories
    matchup = store.matchup_by_period(projection.league_id, projection.period_index)
    if matchup is None:
        return {"safe_total": 0, "safe_held": 0}
    state = store.league_state_asof(projection.league_id, projection.as_of)
    mine = store.category_totals(list(state.rosters.get(projection.team_id, [])),
                                 matchup.period_start, matchup.period_end, cats)
    opp = store.category_totals(list(state.rosters.get(projection.opponent_id, [])),
                                matchup.period_start, matchup.period_end, cats)
    safe_total = safe_held = 0
    for c in cats:
        if projection.categories[c].label == "safe":
            safe_total += 1
            if _wins(mine[c], opp[c], c):
                safe_held += 1
    return {"safe_total": safe_total, "safe_held": safe_held}


def _next(iso_date: str) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(iso_date) + timedelta(days=1)).isoformat()


def replay_season(
    store, league_id: str, config: Config | None = None, offset_days: int = 3
) -> dict:
    """Run the engine's actual top call at each (team, scoring period) decision point over a
    season and grade every one against what really happened — the drift-proof track record.

    The headline metric, ``target_hit_rate``, is **opponent-independent**: did the recommended
    add out-produce the dropped player in the targeted category over the rest of the week
    (from real box scores)? ``helped_rate`` (did the move improve the matchup vs standing pat)
    depends on opponent strength, so it's reported with a caveat while opponents are static.
    """
    from datetime import date, timedelta

    from fantasy_gm.engine.reconcile import Reconciler

    config = config or Config()
    rec = Reconciler(config)
    periods = store.conn.execute(
        """SELECT DISTINCT period_index, period_start, period_end FROM matchups
           WHERE league_id = ? ORDER BY period_index""",
        (league_id,),
    ).fetchall()

    grades = []
    for p in periods:
        as_of = (date.fromisoformat(p["period_start"]) + timedelta(days=offset_days)).isoformat()
        if as_of > p["period_end"]:
            continue
        for team in store.team_ids(league_id):
            moves = rec.reconcile(store, league_id, team, as_of, max_moves=1)
            if not moves:
                continue
            grades.append(grade_move(store, moves[0], config))

    n = len(grades)
    if n == 0:
        return {"moves": 0}
    hit = sum(1 for g in grades if g.target_realized_delta > 0)
    helped = sum(1 for g in grades if g.helped)
    flips = sum(len(g.flipped_to_me) for g in grades)
    unflips = sum(len(g.flipped_away) for g in grades)
    return {
        "moves": n, "hit": hit, "helped": helped, "flips": flips, "unflips": unflips,
        "delta_sum": round(sum(g.target_realized_delta for g in grades), 2),
        "target_hit_rate": round(hit / n, 3),
        "avg_target_delta": round(sum(g.target_realized_delta for g in grades) / n, 2),
        "helped_rate": round(helped / n, 3),
        "net_cats_per_move": round((flips - unflips) / n, 3),
    }
