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


def scoreboard_payload(store, config: Config, league_id: str, as_of: str, *,
                       projector: Projector | None = None) -> dict:
    """The scoreboard tab: every active matchup in the league, side by side.

    Elvin-Feng-style weekly matchup comparisons: one row per matchup, both teams'
    projected end-of-week category totals (the same ``Projector`` numbers the
    matchup tab shows for Tim's own matchup, now league-wide), each with σ and a
    25th–75th band, a per-category win probability, and a projected score that
    counts categories (>0.5 team A, <0.5 team B, exactly 0.5 a tie).

    One ``Projector.project`` call per matchup (six in a 12-team league), never
    one per team — each call prices both sides. Teams without an active matchup
    are left out; the league's matchups are discovered by walking the teams once.
    """
    proj = projector or Projector(config=config)
    names = team_names(store, league_id)
    seen: set[str] = set()
    matchups: list[dict] = []
    for team_id in sorted(names):
        if team_id in seen:
            continue
        matchup = store.matchup_for_team(league_id, team_id, as_of)
        if matchup is None:
            continue
        seen.update({matchup.team_a, matchup.team_b})
        p = proj.project(store, league_id, matchup.team_a, as_of)
        if not p.opponent_id:
            continue
        rows = _cat_rows(p)
        a_wins = sum(1 for r in rows if r["win_prob"] > 0.5)
        b_wins = sum(1 for r in rows if r["win_prob"] < 0.5)
        ties = len(rows) - a_wins - b_wins
        matchups.append({
            "period_index": p.period_index,
            "period_start": matchup.period_start,
            "period_end": matchup.period_end,
            "team_a": {
                "team_id": p.team_id,
                "name": names.get(p.team_id, p.team_id),
                "games_remaining": games_remaining(store, league_id, p.team_id, as_of),
            },
            "team_b": {
                "team_id": p.opponent_id,
                "name": names.get(p.opponent_id, p.opponent_id),
                "games_remaining": games_remaining(store, league_id, p.opponent_id, as_of),
            },
            "categories": rows,
            "projected_score": f"{a_wins}-{b_wins}" + (f"-{ties}" if ties else ""),
        })
    matchups.sort(key=lambda m: (m["period_start"], m["team_a"]["team_id"]))
    return {"as_of": as_of, "league_id": league_id, "matchups": matchups}
