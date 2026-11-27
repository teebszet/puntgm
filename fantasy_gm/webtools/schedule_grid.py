"""T4 — the Hashtag-style advanced schedule grid: NBA teams × upcoming dates.

Rows are the league's NBA teams, columns the next ``days`` dates; each cell is one
scheduled game (``vs OPP`` / ``@OPP``). The cell color is opponent-defense
favorability — the T1 model (``fixture_grid.team_defense``) — aggregated to a
composite (mean of the direction-aware per-category favors), with the per-category
values carried alongside for the hover. A league-wide "# games" row gives each
date's slate size: the streaming-decision view Hashtag's advanced grid is built
for, simplified to our one platform's data (no platform/weekday filters, no
second-week preview columns).

Teams with scheduled games but no defense data yet still render — their cells
carry ``favor: null`` and the page shows them uncolored.
"""

from __future__ import annotations

import datetime as dt

from fantasy_gm.webtools.fixture_grid import _NO_TEAM, _schedule, team_defense


def _composite(favor: dict[str, float]) -> float | None:
    """Mean of the direction-aware favors — one number to color a whole matchup.

    Categories with no data (a rate cat missing everywhere) simply don't divide in;
    an opponent with no data at all composes to ``None``.
    """
    if not favor:
        return None
    return sum(favor.values()) / len(favor)


def schedule_grid_payload(store, as_of: str, *, days: int = 14) -> dict:
    """T4: every NBA team's upcoming games, colored by opponent-defense favorability."""
    defense = team_defense(store, as_of)

    start = dt.date.fromisoformat(as_of)
    dates = [(start + dt.timedelta(days=i)).isoformat() for i in range(days)]
    schedule = _schedule(store, dates[0], dates[-1])

    teams = sorted(
        set(defense.allowed)
        | {side for games in schedule.values() for g in games
           for side in (g["home"], g["away"]) if side not in _NO_TEAM})

    rows = []
    for team in teams:
        cells: dict[str, dict] = {}
        for date in dates:
            for g in schedule.get(date, ()):
                if team not in (g["home"], g["away"]):
                    continue
                opp = g["away"] if team == g["home"] else g["home"]
                favor = defense.favor.get(opp, {})
                cells[date] = {
                    "opponent": opp,
                    "is_home": team == g["home"],
                    "favor": round(_composite(favor), 3) if favor else None,
                    "favor_by_cat": {c: round(v, 2)
                                     for c, v in sorted(favor.items())},
                }
        rows.append({"team": team, "games": len(cells), "cells": cells})

    return {
        "as_of": as_of,
        "days": days,
        "dates": dates,
        "league_games_by_date": [len(schedule.get(d, ())) for d in dates],
        "team_basis": defense.team_basis,
        "teams": rows,
    }
