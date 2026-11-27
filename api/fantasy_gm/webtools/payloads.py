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

from fantasy_gm.config import CATEGORY_DIRECTION, PERCENTAGE_CATEGORIES, Config
from fantasy_gm.engine.projection import Projector, _label, _phi
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


def _team_sort(team_id: str) -> tuple:
    """Natural order for stored team ids: ``….t.2`` sorts before ``….t.10``."""
    head, _, tail = team_id.rpartition(".")
    return (head, int(tail)) if tail.isdigit() else (team_id, 0)


def _pairkey(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _completed_week_totals(store, config: Config, league_id: str, period: dict,
                           names: dict[str, str], actuals: dict[str, dict],
                           ) -> dict[str, dict[str, float]]:
    """team_id -> {cat: comparable value} for a completed week.

    Yahoo's own weekly totals when backfilled (exact — no attribution involved);
    otherwise the store's banked tally over the week's date range, rate cats
    volume-weighted from their make/attempt components.
    """
    out: dict[str, dict[str, float]] = {}
    rosters: dict[str, list] | None = None
    for tid in names:
        if tid in actuals:
            out[tid] = {c: (float(actuals[tid].get(c, 0.0)), 0.0) for c in config.categories}
            continue
        if rosters is None:
            rosters = store.league_state_asof(league_id, period["end"]).rosters
        # The projector's own pattern (team_projection._side): request the component
        # keys alongside the categories — banked totals only carry what you ask for.
        comp_keys = sorted({k for pair in PERCENTAGE_CATEGORIES.values() for k in pair})
        totals = store.banked_totals_for_team(
            league_id, tid, period["start"], period["end"],
            list(config.categories) + comp_keys,
            fallback_ids=rosters.get(tid, []),
        )
        row = {}
        for c in config.categories:
            if c in PERCENTAGE_CATEGORIES:
                mk, at = PERCENTAGE_CATEGORIES[c]
                row[c] = ((totals[mk] / totals[at]) if totals[at] else 0.0, 0.0)
            else:
                row[c] = (float(totals[c]), 0.0)
        out[tid] = row
    return out


def _live_week_projections(store, config: Config, league_id: str, as_of: str,
                           names: dict[str, str],
                           projector: Projector | None) -> tuple[dict, set]:
    """team_id -> {cat: (projected end-of-week total, σ)} for the live week, plus the
    set of real matchup pairs. One engine pass per real matchup (six in a 12-team
    league) — a team's projection is opponent-independent, so both sides feed the
    all-pairs matrix. Totals are UNROUNDED (``Projector.project_side``): the pair
    win probability below must run on the same numbers ``_assemble`` runs on, or a
    sharp category's cell drifts from the Matchup tab's own figure."""
    proj = projector or Projector(config=config)
    state = store.league_state_asof(league_id, as_of)
    out: dict[str, dict[str, tuple[float, float]]] = {}
    real: set[tuple[str, str]] = set()
    for tid in sorted(names, key=_team_sort):
        if tid in out:
            continue
        m = store.matchup_for_team(league_id, tid, as_of)
        if m is None:
            continue
        real.add(_pairkey(m.team_a, m.team_b))
        for team in (m.team_a, m.team_b):
            if team not in out:
                out[team] = proj.project_side(store, league_id, team, as_of,
                                              state, m)[0]
    return out, real


def scoreboard_matrix_payload(store, config: Config, league_id: str, as_of: str, *,
                              week: int | None = None,
                              projector: Projector | None = None) -> dict:
    """The scoreboard tab: every team against every team for one matchup week.

    Tim's 2026-11-27 correction of the first cut (which showed only the six real
    matchups): a full round-robin matrix — each cell the 9-cat record between two
    teams — with a selector over historical weeks including the live one.

    Completed weeks read Yahoo's own weekly team totals where they have been
    backfilled into ``matchup_team_stats`` (exact, no roster attribution involved)
    and fall back to the store's banked tally otherwise — the source is stated so a
    fallback week is never mistaken for ground truth. The live week projects every
    team with the same ``Projector`` numbers the Matchup tab shows: a real matchup's
    cell stays number-identical to that tab, and any other pair is priced with the
    engine's own Φ((a−b)/√(σa²+σb²)) per-category win probability.

    Raises ``ValueError`` for a week that is neither stored nor live-or-earlier.
    """
    cats = list(config.categories)
    periods = store.matchup_periods(league_id)
    if not periods:
        raise ValueError(f"no matchup periods stored for {league_id}")
    live_week = next((p["week"] for p in periods
                      if p["start"] <= as_of <= p["end"]), None)
    if live_week is None:
        started = [p["week"] for p in periods if p["start"] <= as_of]
        live_week = max(started) if started else periods[0]["week"]
    if week is None:
        week = live_week
    sel = next((p for p in periods if p["week"] == week), None)
    if sel is None or week > live_week:
        raise ValueError(f"week {week} is not a completed or live week")

    names = team_names(store, league_id)
    order = sorted(names, key=_team_sort)
    weeks = [{"week": p["week"], "start": p["start"], "end": p["end"],
              "status": "live" if p["week"] == live_week else "completed"}
             for p in periods if p["week"] <= live_week]

    real_pairs: set[tuple[str, str]] = set()
    if week == live_week:
        per_team, real_pairs = _live_week_projections(
            store, config, league_id, as_of, names, projector)
        source = "projected"
    else:
        actuals = store.matchup_team_stats_for_period(league_id, week)
        per_team = _completed_week_totals(store, config, league_id, sel, names, actuals)
        source = "yahoo" if len(actuals) >= len(names) else "store"
    # A team with no live matchup carries no projection — the matrix renders priced
    # teams only (the completed-week path prices everyone from the box scores).
    order = [t for t in order if t in per_team]
    if not order:
        raise ValueError("no priced teams for this week")

    pairs = []
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            a_wins = b_wins = ties = 0
            rows = []
            for c in cats:
                at, asd = per_team[a][c]
                bt, bsd = per_team[b][c]
                direction = CATEGORY_DIRECTION[c]
                row: dict = {"category": c, "a": _round_cat(c, at), "b": _round_cat(c, bt)}
                if source == "projected":
                    combined = math.hypot(asd, bsd)
                    diff = direction * (at - bt)
                    wp = (1.0 if diff > 0 else 0.5 if diff == 0 else 0.0) if combined == 0.0 \
                        else _phi(diff / combined)
                    row.update({"a_std": _round_cat(c, asd), "b_std": _round_cat(c, bsd),
                                "win_prob": round(wp, 2), "label": _label(wp)})
                    a_won, b_won = wp > 0.5, wp < 0.5
                else:
                    margin = direction * (at - bt)
                    # Completed weeks decide at any nonzero margin — a 0.003 fg% edge
                    # is a win (the win-prob branch above is the one that uses 0.5).
                    a_won, b_won = margin > 0, margin < 0
                if a_won:
                    a_wins += 1
                elif b_won:
                    b_wins += 1
                else:
                    ties += 1
                rows.append(row)
            record = f"{a_wins}-{b_wins}" + (f"-{ties}" if ties else "")
            pairs.append({"a": a, "b": b, "record": record,
                          "real": _pairkey(a, b) in real_pairs, "cats": rows})

    teams = [{"team_id": t, "name": names[t]} for t in order]
    if source == "projected":
        for t in teams:
            t["games_remaining"] = games_remaining(store, league_id, t["team_id"], as_of)
    return {
        "as_of": as_of, "league_id": league_id,
        "week": sel["week"], "start": sel["start"], "end": sel["end"],
        "status": "live" if sel["week"] == live_week else "completed",
        "source": source, "weeks": weeks, "cats": cats, "teams": teams, "pairs": pairs,
    }
