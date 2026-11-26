"""Instrumented replay — decomposed track record + strategy baselines.

``engine.scoring.replay_season`` answers "how often did the engine's call work?" with a
single number. This module answers *why*, and against *what*:

* **Decomposition** — every graded call splits into hit / tie / miss (the single-number
  version silently counts a tie as a miss) and breaks down per target category, so a bad
  headline rate can be traced to the categories causing it.
* **Baselines** — naive strategies run through the *same* decision slots, so the claim
  becomes **lift over what a manager would do by default** rather than an absolute rate
  with no reference point. 43% means nothing alone; 43% against a 55% "just take the guy
  with the most games" baseline means the engine is actively harmful.

A **decision slot** is ``(league, team, as-of, target category, dropped player)``. The
engine chooses the target category and the drop; each strategy then proposes an *add* for
that same slot. Holding the slot fixed isolates the add decision — the part the wire model
is responsible for — so strategies are compared on equal terms.

Percentage categories are graded in **volume-weighted impact** units
(``Δmakes − league_rate × Δattempts``), not as a roster-level ratio difference: the ratio
form is diluted by the eight players who didn't change and is undefined at zero attempts,
which made every percentage-targeted call effectively ungradeable.
"""

from __future__ import annotations

import json
import random
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from fantasy_gm.config import CATEGORY_DIRECTION, PERCENTAGE_CATEGORIES, Config

_EPS = 1e-9


def _next(iso_date: str) -> str:
    return (date.fromisoformat(iso_date) + timedelta(days=1)).isoformat()


@dataclass
class Slot:
    """One decision point, with the engine's own choice recorded alongside it."""

    league_id: str
    team_id: str
    as_of: str
    period_index: int
    period_end: str
    target_cat: str
    drop_id: str
    engine_add_id: str
    candidates: list[str] = field(default_factory=list)  # legal wire adds at this moment


@dataclass
class Graded:
    strategy: str
    slot: Slot
    add_id: str
    delta: float          # direction-adjusted add-minus-drop realized production
    outcome: str          # "hit" | "tie" | "miss"
    add_games: int        # games the add actually played over the rest of the period
    drop_games: int


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------
def league_rates(store, season: str) -> dict[str, float]:
    """Pool-wide makes/attempts rate per percentage category — the replacement level a
    percentage-category add is judged against."""
    from fantasy_gm.valuation import _player_games, rosterable_pool

    games = _player_games(store, season)
    pool = rosterable_pool(store, season, games=games)
    out: dict[str, float] = {}
    for cat, (mk, at) in PERCENTAGE_CATEGORIES.items():
        made = sum(g.get(mk, 0.0) for p in pool for g in games[p])
        att = sum(g.get(at, 0.0) for p in pool for g in games[p])
        out[cat] = made / att if att > 0 else 0.0
    return out


def grade(store, slot: Slot, add_id: str, strategy: str, rates: dict[str, float]) -> Graded:
    """Grade one proposed add against the slot's drop, over the rest of the period.

    Opponent-independent by construction: it only asks whether the added player
    out-produced the dropped one in the targeted category from real box scores.
    """
    cat = slot.target_cat
    start, end = _next(slot.as_of), slot.period_end

    if cat in PERCENTAGE_CATEGORIES:
        mk, at = PERCENTAGE_CATEGORIES[cat]
        a = store.category_totals([add_id], start, end, [mk, at])
        d = store.category_totals([slot.drop_id], start, end, [mk, at]) if slot.drop_id else {}
        p = rates.get(cat, 0.0)
        # volume-weighted impact: makes above what a league-average shooter produces on the
        # same attempts. Positive = the swap raises the roster's rate.
        delta = ((a.get(mk, 0.0) - p * a.get(at, 0.0))
                 - (d.get(mk, 0.0) - p * d.get(at, 0.0)))
    else:
        a = store.category_totals([add_id], start, end, [cat])
        d = store.category_totals([slot.drop_id], start, end, [cat]) if slot.drop_id else {}
        delta = CATEGORY_DIRECTION[cat] * (a.get(cat, 0.0) - d.get(cat, 0.0))

    outcome = "hit" if delta > _EPS else ("miss" if delta < -_EPS else "tie")
    return Graded(strategy, slot, add_id, round(delta, 4), outcome,
                  _games_played(store, add_id, start, end),
                  _games_played(store, slot.drop_id, start, end) if slot.drop_id else 0)


def _games_played(store, player_id: str, start: str, end: str) -> int:
    row = store.conn.execute(
        """SELECT COUNT(*) n FROM player_logs
           WHERE player_id = ? AND game_date >= ? AND game_date <= ?""",
        (player_id, start, end),
    ).fetchone()
    return int(row["n"])


# --------------------------------------------------------------------------
# strategies: (store, slot, ctx) -> add_id | None
# --------------------------------------------------------------------------
def strat_engine(store, slot, ctx):
    return slot.engine_add_id


def strat_random(store, slot, ctx):
    rng = random.Random(f"{slot.league_id}|{slot.team_id}|{slot.as_of}|{slot.target_cat}")
    return rng.choice(slot.candidates) if slot.candidates else None


def strat_top_value(store, slot, ctx):
    """Best available player by season-long 9-cat z-value — the "just take the best guy
    on the wire" heuristic most tools effectively implement."""
    values = ctx["values"]
    return max(slot.candidates, key=lambda p: values.get(p, -999.0), default=None)


def strat_most_games(store, slot, ctx):
    """Most remaining games in the period — the naive streaming heuristic, applied to the
    whole wire. Mostly selects deep-bench players on busy teams who never take the floor,
    which is the point: it separates *scheduled* games from *played* ones."""
    return max(slot.candidates,
               key=lambda p: ctx["remaining"].get((p, slot.as_of, slot.period_end), 0),
               default=None)


def strat_rotation_games(store, slot, ctx, min_minutes: float = 20.0):
    """Most remaining games **among players who actually play rotation minutes** — what a
    competent manager does by hand: filter to people in the rotation, then count games.

    This is the baseline the engine has to beat to claim anything. ``most_games`` is a
    strawman (it drafts end-of-bench players); this one is not.
    """
    mins = ctx["minutes"].get(slot.as_of, {})
    eligible = [p for p in slot.candidates if mins.get(p, 0.0) >= min_minutes]
    if not eligible:  # thin wire — fall back to the best-played players available
        eligible = sorted(slot.candidates, key=lambda p: -mins.get(p, 0.0))[:10]
    return max(eligible,
               key=lambda p: (ctx["remaining"].get((p, slot.as_of, slot.period_end), 0),
                              mins.get(p, 0.0)),
               default=None)


def strat_recent_cat(store, slot, ctx):
    """Highest trailing-N mean in the target category — the engine's own shortlist rule,
    without the re-projection ranking on top. Isolates whether re-projection adds value
    over a raw recency sort."""
    window = ctx["window"]
    best, best_v = None, None
    for pid in slot.candidates:
        v = _recent(store, pid, slot.as_of, slot.target_cat, window) * _dir(slot.target_cat)
        if best_v is None or v > best_v:
            best, best_v = pid, v
    return best


def strat_recent_cat_q(store, slot, ctx):
    """``recent_cat`` weighted by participation rate (A13) — the isolated A/B for the
    games-played model. Identical slot, identical shortlist rule, the only difference being
    that production is counted per *scheduled* game rather than per *played* game."""
    window, pwin = ctx["window"], ctx["participation_window"]
    best, best_v = None, None
    for pid in slot.candidates:
        v = _recent(store, pid, slot.as_of, slot.target_cat, window) * _dir(slot.target_cat)
        if slot.target_cat not in PERCENTAGE_CATEGORIES:
            q = store.participation_rate(pid, slot.as_of, window=pwin)
            v *= 1.0 if q is None else q
        if best_v is None or v > best_v:
            best, best_v = pid, v
    return best


def _dir(cat: str) -> int:
    return 1 if cat in PERCENTAGE_CATEGORIES else CATEGORY_DIRECTION[cat]


def _recent(store, pid, as_of, cat, window) -> float:
    logs = store.player_logs_asof(as_of, player_id=pid)[-window:]
    if not logs:
        return 0.0
    if cat in PERCENTAGE_CATEGORIES:
        mk, at = PERCENTAGE_CATEGORIES[cat]
        made = sum(lg.stats.get(mk, 0.0) for lg in logs)
        att = sum(lg.stats.get(at, 0.0) for lg in logs)
        return made / att if att > 0 else 0.0
    return sum(lg.stats.get(cat, 0.0) for lg in logs) / len(logs)


STRATEGIES = {
    "engine": strat_engine,
    "recent_cat": strat_recent_cat,
    "recent_cat_q": strat_recent_cat_q,
    "top_value": strat_top_value,
    "rotation_games": strat_rotation_games,
    "most_games": strat_most_games,
    "random": strat_random,
}


def trailing_minutes(store, as_of: str, window: int = 10) -> dict[str, float]:
    """Average minutes over each player's last ``window`` games known before ``as_of``.

    One query per as-of date rather than per player: the candidate pool is ~400 deep and
    the same dates repeat across teams, so per-player lookups dominated the run.
    """
    rows = store.conn.execute(
        """SELECT player_id, AVG(minutes) m FROM (
               SELECT player_id, minutes,
                      ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY known_from DESC) rn
               FROM usage_role WHERE known_from <= ?
           ) WHERE rn <= ? GROUP BY player_id""",
        (as_of, window),
    )
    return {r["player_id"]: r["m"] or 0.0 for r in rows}


# --------------------------------------------------------------------------
# slot extraction (expensive — runs the real engine) + caching
# --------------------------------------------------------------------------
def extract_slots(
    store, league_id: str, config: Config | None = None, offset_days: int = 3,
    max_candidates: int = 400,
) -> list[Slot]:
    """Replay the season and record every decision slot where the engine made a call."""
    from fantasy_gm.engine.reconcile import Reconciler

    config = config or Config()
    rec = Reconciler(config)
    periods = store.conn.execute(
        """SELECT DISTINCT period_index, period_start, period_end FROM matchups
           WHERE league_id = ? ORDER BY period_index""",
        (league_id,),
    ).fetchall()

    season = store.league_meta(league_id)["season"]
    slots: list[Slot] = []
    for p in periods:
        as_of = (date.fromisoformat(p["period_start"])
                 + timedelta(days=offset_days)).isoformat()
        if as_of > p["period_end"]:
            continue
        for team in store.team_ids(league_id):
            moves = rec.reconcile(store, league_id, team, as_of, max_moves=1)
            if not moves:
                continue
            m = moves[0]
            target = (m.line_of_play or "").split()[-1].lower()
            if target not in config.categories:
                continue
            state = store.league_state_asof(league_id, as_of)
            rostered = state.rostered_player_ids()
            cands = []
            for pid, _name, _t in store.player_universe(season, as_of):
                if pid in rostered:
                    continue
                nba_team = store.player_team(pid, as_of)
                if nba_team and store.remaining_games_for_team(
                        nba_team, as_of, p["period_end"]) > 0:
                    cands.append(pid)
            slots.append(Slot(
                league_id, team, as_of, int(p["period_index"]), p["period_end"],
                target, m.drop_id, m.add_id, cands[:max_candidates],
            ))
    return slots


def save_slots(slots: list[Slot], path: str) -> None:
    with open(path, "w") as fh:
        json.dump([asdict(s) for s in slots], fh)


def load_slots(path: str) -> list[Slot]:
    with open(path) as fh:
        return [Slot(**d) for d in json.load(fh)]


# --------------------------------------------------------------------------
# the comparison run
# --------------------------------------------------------------------------
def run_strategies(store, slots: list[Slot], season: str,
                   config: Config | None = None,
                   strategies: dict | None = None) -> dict[str, list[Graded]]:
    from fantasy_gm.valuation import player_values

    config = config or Config()
    strategies = strategies or STRATEGIES
    rates = league_rates(store, season)

    remaining: dict = {}
    for s in slots:
        for pid in s.candidates:
            key = (pid, s.as_of, s.period_end)
            if key not in remaining:
                t = store.player_team(pid, s.as_of)
                remaining[key] = (store.remaining_games_for_team(t, s.as_of, s.period_end)
                                  if t else 0)
    minutes = {d: trailing_minutes(store, d, config.recent_games_window)
               for d in {s.as_of for s in slots}}
    ctx = {"values": player_values(store, season), "remaining": remaining,
           "minutes": minutes, "window": config.recent_games_window,
           "participation_window": config.participation_window}

    out: dict[str, list[Graded]] = {}
    for name, fn in strategies.items():
        graded = []
        for s in slots:
            add = fn(store, s, ctx)
            if add:
                graded.append(grade(store, s, add, name, rates))
        out[name] = graded
    return out


def summarize(graded: list[Graded]) -> dict:
    n = len(graded)
    if n == 0:
        return {"n": 0}
    hits = sum(1 for g in graded if g.outcome == "hit")
    ties = sum(1 for g in graded if g.outcome == "tie")
    misses = sum(1 for g in graded if g.outcome == "miss")
    deltas = [g.delta for g in graded]
    decided = hits + misses
    return {
        "n": n,
        "hit": hits, "tie": ties, "miss": misses,
        "hit_rate": round(hits / n, 3),
        # ties are usually "neither player took the floor" — excluding them measures the
        # decision rather than the schedule.
        "hit_rate_decided": round(hits / decided, 3) if decided else None,
        "avg_delta": round(statistics.fmean(deltas), 3),
        "median_delta": round(statistics.median(deltas), 3),
        "add_dnp": sum(1 for g in graded if g.add_games == 0),
        "drop_dnp": sum(1 for g in graded if g.drop_games == 0),
        "se": round((0.25 / decided) ** 0.5, 4) if decided else None,
    }


def by_category(graded: list[Graded]) -> dict[str, dict]:
    buckets: dict[str, list[Graded]] = {}
    for g in graded:
        buckets.setdefault(g.slot.target_cat, []).append(g)
    return {c: summarize(v) for c, v in sorted(buckets.items())}
