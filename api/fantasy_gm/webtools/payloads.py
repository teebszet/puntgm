"""Read-only JSON payloads for the puntgm.com decision tools.

The site's backend (api/, deployed on Vercel) imports this module and serves each
payload as JSON. Everything here is a pure function of the store snapshot: no Yahoo
network calls, no writes — the Mac watcher stays the only writer, and a deployed
snapshot is a faithful picture of the 14:30 HKT run.

Tool 2 (projected matchup table) wraps ``Projector`` — the same engine the daily
digest uses — but, unlike the digest, publishes the variance the engine already
computes: per-category σ for both sides and a stated 25th–75th percentile band.
Tim's 2026-11-23 verdict on the digests ("the projections are so off") was about
point estimates pretending to certainty; this payload makes the uncertainty
first-class so a coin-flip category reads like a coin flip.
"""

from __future__ import annotations

import math

from fantasy_gm.config import PERCENTAGE_CATEGORIES, Config
from fantasy_gm.engine.projection import Projector
from fantasy_gm.models import MatchupProjection

# 25th/75th percentile of a normal distribution sits at ±0.6745σ.
_P75_Z = 0.6744897501960817

# Rate categories live on a 0-1 scale: round rates to 3 decimals, counting cats to 1.
_RATE_CATS = frozenset(PERCENTAGE_CATEGORIES)


def _round_cat(cat: str, value: float) -> float:
    return round(value, 3 if cat in _RATE_CATS else 1)


def team_names(store, league_id: str) -> dict[str, str]:
    """team_id -> display name for every team in the league."""
    return {
        row["team_id"]: row["team_name"]
        for row in store.conn.execute(
            "SELECT team_id, team_name FROM teams WHERE league_id = ?", (league_id,))
    }


def games_remaining(store, league_id: str, team_id: str, as_of: str) -> int | None:
    """Yahoo-style games remaining for one team over its current matchup week.

    Same settled rule as the digest (``scripts/daily_gm_run.py::games_remaining``,
    verified live vs Yahoo 2026-10-26 and re-settled 2026-11-02): per remaining
    date, the players slotted into starting slots that date whose NBA team plays
    that date; bench, IL, and OUT never count. Returns ``None`` when the team has
    no active matchup in the store.
    """
    matchup = store.matchup_for_team(league_id, team_id, as_of)
    if matchup is None:
        return None
    counts = store.scheduled_active_games(league_id, team_id, as_of, matchup.period_end)
    if counts is None:
        # No per-date lineup coverage — fall back to the current-lineup starters,
        # the digest's legacy rule, so the tool never silently shows zero.
        from fantasy_gm.data.store import NON_ACCRUING_SLOTS

        counts = {}
        for pid, slot in store.lineup_asof(league_id, team_id, as_of).items():
            if slot in NON_ACCRUING_SLOTS:
                continue
            nba_team = store.player_team(pid, as_of)
            if nba_team:
                rg = store.remaining_games_for_team(nba_team, as_of, matchup.period_end)
                if rg:
                    counts[pid] = rg
    return sum(counts.values())


def _cat_rows(proj: MatchupProjection) -> list[dict]:
    """One row per category with both sides' band, ordered as the config lists them."""
    rows = []
    for cat, p in proj.categories.items():
        r = _round_cat
        rows.append({
            "category": cat,
            "mine": r(cat, p.mine_total),
            "mine_std": r(cat, p.mine_std),
            "mine_lo": r(cat, p.mine_total - _P75_Z * p.mine_std),
            "mine_hi": r(cat, p.mine_total + _P75_Z * p.mine_std),
            "opp": r(cat, p.opp_total),
            "opp_std": r(cat, p.opp_std),
            "opp_lo": r(cat, p.opp_total - _P75_Z * p.opp_std),
            "opp_hi": r(cat, p.opp_total + _P75_Z * p.opp_std),
            "win_prob": round(p.win_prob, 2),
            "label": p.label,
        })
    return rows


def matchup_payload(store, config: Config, league_id: str, team_id: str, as_of: str,
                    *, projector: Projector | None = None) -> dict | None:
    """Tool 2: the current matchup, both sides as distributions, per category.

    ``None`` when the team has no active matchup on ``as_of``.
    """
    proj = (projector or Projector(config=config)).project(
        store, league_id, store.resolve_team_id(league_id, team_id) or team_id, as_of)
    if not proj.opponent_id:
        return None
    names = team_names(store, league_id)
    team_id = proj.team_id
    matchup = store.matchup_for_team(league_id, team_id, as_of)
    return {
        "as_of": proj.as_of,
        "league_id": league_id,
        "team_id": team_id,
        "team_name": names.get(team_id, team_id),
        "opponent_id": proj.opponent_id,
        "opponent_name": names.get(proj.opponent_id, proj.opponent_id),
        "period_index": proj.period_index,
        "period_start": matchup.period_start,
        "period_end": matchup.period_end,
        "games_remaining": {
            "mine": games_remaining(store, league_id, team_id, as_of),
            "opp": games_remaining(store, league_id, proj.opponent_id, as_of),
        },
        "categories": _cat_rows(proj),
    }


def teams_payload(store, league_id: str) -> list[dict]:
    """Tool 3's picker: every team, id + name."""
    return [
        {"team_id": tid, "name": name}
        for tid, name in sorted(team_names(store, league_id).items())
    ]
