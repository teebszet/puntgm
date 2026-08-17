"""Draft replay: strategies drafted against each other over a season that already happened.

This is the measuring stick, and it is what lets the optimizer be validated before any
forward projection exists (design D11). Strategies draft from the real player pool; the
resulting rosters are then scored on **realized weekly category results** from the actual box
scores. Nothing is forecast — the season is known — so the only thing under test is the
drafting.

**Grading is opponent-symmetric.** Every team plays every other team every week, and a
strategy's score is its category win rate across all those pairings. A round-robin *schedule*
would inject luck (who you happened to draw in a good week) and, worse, would make results
depend on the schedule generator. All-play-all removes both.

**Draft position is controlled for** by rotating the seat assignment: with ``rotations=k``,
each strategy is placed at ``k`` different seats and results are reported per seat as well as
pooled. Without this, a strategy that happened to draft first would look better than it is —
the published H₀ results show win rate varying strongly with draft position.

The z-score strategy is the incumbent this must beat; ADP is the "just follow the board"
control.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from fantasy_gm.config import CATEGORY_DIRECTION, PERCENTAGE_CATEGORIES
from fantasy_gm.draft.hscore import DraftState, HScoreEngine, OpponentModel
from fantasy_gm.draft.opponents import AdpBot, derive_adp_order
from fantasy_gm.draft.settings import DraftSettings
from fantasy_gm.draft.xscore import XScoreBasis


@dataclass
class StrategyResult:
    """One strategy's realized performance."""

    name: str
    category_wins: float = 0.0
    category_games: int = 0
    matchup_wins: float = 0.0
    matchups: int = 0
    per_category: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(lambda: [0.0, 0])
    )
    by_seat: dict[int, list[float]] = field(
        default_factory=lambda: defaultdict(lambda: [0.0, 0])
    )

    @property
    def category_win_rate(self) -> float:
        return self.category_wins / self.category_games if self.category_games else 0.0

    @property
    def matchup_win_rate(self) -> float:
        return self.matchup_wins / self.matchups if self.matchups else 0.0

    def category_rates(self) -> dict[str, float]:
        return {c: (w / n if n else 0.0) for c, (w, n) in self.per_category.items()}

    def seat_rates(self) -> dict[int, float]:
        return {s: (w / n if n else 0.0) for s, (w, n) in sorted(self.by_seat.items())}


# --- strategies ---------------------------------------------------------------


def hscore_strategy(engine: HScoreEngine):
    def pick(state: DraftState, available: list[str]) -> str | None:
        best = engine.best_pick(state, available)
        return best.player_id if best else (available[0] if available else None)

    return pick


def static_order_strategy(order: list[str]):
    """Draft straight down a fixed board — used for G-score, z-score, and ADP."""
    rank = {pid: i for i, pid in enumerate(order)}

    def pick(state: DraftState, available: list[str]) -> str | None:
        if not available:
            return None
        return min(available, key=lambda p: (rank.get(p, 10**9), p))

    return pick


def bot_strategy(bot: AdpBot):
    def pick(state: DraftState, available: list[str]) -> str | None:
        return bot.pick(available)

    return pick


# --- drafting -----------------------------------------------------------------


def snake_draft(
    strategies: list,
    pool: list[str],
    settings: DraftSettings,
) -> list[list[str]]:
    """Run a snake draft. ``strategies[seat]`` is a callable ``(state, available) -> pid``."""
    n_teams = len(strategies)
    rosters: list[list[str]] = [[] for _ in range(n_teams)]
    taken: set[str] = set()
    for rnd in range(settings.n_rounds):
        order = range(n_teams) if rnd % 2 == 0 else reversed(range(n_teams))
        for seat in order:
            available = [p for p in pool if p not in taken]
            if not available:
                continue
            state = DraftState(
                my_roster=list(rosters[seat]),
                opponent_rosters=[list(r) for i, r in enumerate(rosters) if i != seat],
                taken=set(taken),
            )
            pid = strategies[seat](state, available)
            if pid is None:
                continue
            rosters[seat].append(pid)
            taken.add(pid)
    return rosters


# --- realized grading ---------------------------------------------------------


def _iso_week(day: str) -> str:
    y, w, _ = date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def weekly_totals(
    store, season: str, categories: list[str]
) -> dict[str, dict[str, dict[str, float]]]:
    """``player -> week -> {stat_key: realized total}`` over the completed season."""
    needed: set[str] = set()
    for c in categories:
        if c in PERCENTAGE_CATEGORIES:
            needed.update(PERCENTAGE_CATEGORIES[c])
        else:
            needed.add(c)
    out: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in store.conn.execute(
        "SELECT player_id, game_date, stats_json FROM player_logs WHERE season = ?", (season,)
    ):
        stats = json.loads(r["stats_json"])
        wk = out[r["player_id"]][_iso_week(r["game_date"])]
        for k in needed:
            wk[k] = wk.get(k, 0.0) + float(stats.get(k, 0.0))
    return out


def _team_week(roster: list[str], week: str, totals, categories: list[str]) -> dict[str, float]:
    agg: dict[str, float] = defaultdict(float)
    for pid in roster:
        wk = totals.get(pid, {}).get(week)
        if not wk:
            continue
        for k, v in wk.items():
            agg[k] += v
    out: dict[str, float] = {}
    for c in categories:
        if c in PERCENTAGE_CATEGORIES:
            mk, at = PERCENTAGE_CATEGORIES[c]
            out[c] = agg[mk] / agg[at] if agg[at] > 0 else 0.0
        else:
            out[c] = agg[c]
    return out


def round_robin_pairings(n_teams: int, week_index: int) -> list[tuple[int, int]]:
    """One week of a rotating round-robin schedule (circle method).

    Seat 0 is held fixed while the rest rotate, so over ``n_teams − 1`` weeks every team plays
    every other exactly once. Used by the schedule-based grading arm; with an odd team count
    the last seat in the rotation draws a bye.
    """
    idx = list(range(n_teams))
    if n_teams % 2:
        idx.append(-1)                       # phantom seat: whoever draws it has a bye
    n = len(idx)
    fixed, rot = idx[0], idx[1:]
    k = week_index % (n - 1)
    order = [fixed] + rot[k:] + rot[:k]
    pairs = [(order[i], order[n - 1 - i]) for i in range(n // 2)]
    return [(a, b) for a, b in pairs if a != -1 and b != -1]


def score_rosters(
    store,
    season: str,
    rosters: list[list[str]],
    settings: DraftSettings,
    schedule: bool = False,
) -> list[dict]:
    """Weekly grading of realized production. Returns per-seat results.

    Two gradings, and the difference between them is itself a measurement (task 3.13):

    * **all-play-all** (default) — every team vs every other, every week. Removes schedule luck
      and any dependence on the schedule generator.
    * **schedule** — a rotating round-robin, one opponent per week, which is what a real league
      plays. All-play-all scores a team against the *average* of the field, and that may
      systematically penalise concentrated builds: punting is a strategy for beating one
      opponent at a time, and averaging over eleven is exactly the operation that erases its
      advantage. This arm is how we find out rather than argue.
    """
    cats = settings.categories
    totals = weekly_totals(store, season, cats)
    weeks = sorted({w for p in totals.values() for w in p})
    n = len(rosters)
    per_seat = [
        {"cat_wins": 0.0, "cat_games": 0, "matchup_wins": 0.0, "matchups": 0,
         "per_cat": defaultdict(lambda: [0.0, 0])}
        for _ in range(n)
    ]
    for wi, week in enumerate(weeks):
        week_tot = [_team_week(r, week, totals, cats) for r in rosters]
        pairings = (
            round_robin_pairings(n, wi) if schedule
            else [(a, b) for a in range(n) for b in range(a + 1, n)]
        )
        for a, b in pairings:
            won_a = 0.0
            for c in cats:
                va, vb = week_tot[a][c], week_tot[b][c]
                direction = 1 if c in PERCENTAGE_CATEGORIES else CATEGORY_DIRECTION[c]
                if va == vb:
                    pa = 0.5
                else:
                    pa = 1.0 if (direction * (va - vb)) > 0 else 0.0
                won_a += pa
                per_seat[a]["per_cat"][c][0] += pa
                per_seat[a]["per_cat"][c][1] += 1
                per_seat[b]["per_cat"][c][0] += 1.0 - pa
                per_seat[b]["per_cat"][c][1] += 1
            per_seat[a]["cat_wins"] += won_a
            per_seat[b]["cat_wins"] += len(cats) - won_a
            per_seat[a]["cat_games"] += len(cats)
            per_seat[b]["cat_games"] += len(cats)
            half = len(cats) / 2.0
            res_a = 1.0 if won_a > half else (0.5 if won_a == half else 0.0)
            per_seat[a]["matchup_wins"] += res_a
            per_seat[b]["matchup_wins"] += 1.0 - res_a
            per_seat[a]["matchups"] += 1
            per_seat[b]["matchups"] += 1
    return per_seat


# --- the harness --------------------------------------------------------------


def build_strategies(
    store,
    season: str,
    basis: XScoreBasis,
    settings: DraftSettings,
    rng: random.Random,
    engine_steps: int = 8,
    opponent_arms: tuple[OpponentModel, ...] = (OpponentModel.REPRESENTATIVE,),
) -> dict:
    """The field: H₀ vs G-score vs z-score vs ADP.

    ``opponent_arms`` enters one H₀ per opponent model, so the stand-in and the field objective
    draft in the *same* room against the *same* bots and are graded on the same weeks. Running
    them in separate replays would confound the comparison with the pool each happened to face.
    """
    from fantasy_gm.valuation import player_values

    adp_order = derive_adp_order(store, season)
    g_order = [p for p, _, _ in sorted(
        ((p, basis.total(p), None) for p in basis.pool), key=lambda r: -r[1]
    )]
    z = player_values(store, season)
    z_order = sorted(z, key=lambda p: (-z[p], p))
    out = {
        "g_score": static_order_strategy(g_order),
        "z_score": static_order_strategy(z_order),
        "adp": bot_strategy(AdpBot(adp_order, rng)),
    }
    for arm in opponent_arms:
        name = "h_score" if arm is OpponentModel.REPRESENTATIVE else f"h_score_{arm.value}"
        out[name] = hscore_strategy(
            HScoreEngine(basis, settings, steps=engine_steps, opponent_model=arm)
        )
    return out


def run_draft_replay(
    store,
    season: str,
    basis: XScoreBasis,
    settings: DraftSettings | None = None,
    rotations: int | None = None,
    seed: int = 7,
    engine_steps: int = 8,
    pool_size: int | None = None,
    opponent_arms: tuple[OpponentModel, ...] = (OpponentModel.REPRESENTATIVE,),
    schedule: bool = False,
) -> dict[str, StrategyResult]:
    """Draft and grade every strategy at several seats.

    Each rotation shifts the seat assignment by one, so a strategy's advantage cannot come
    from a favourable draft slot. Remaining seats are filled with ADP bots so the room is a
    realistic size.
    """
    settings = settings or DraftSettings()
    rng = random.Random(seed)
    strategies = build_strategies(
        store, season, basis, settings, rng, engine_steps, opponent_arms
    )
    names = list(strategies)
    n_teams = settings.n_teams
    rotations = rotations if rotations is not None else min(n_teams, 4)

    pool = [p for p, _, _ in sorted(
        ((p, basis.total(p), None) for p in basis.pool), key=lambda r: -r[1]
    )]
    if pool_size:
        pool = pool[:pool_size]

    results = {n: StrategyResult(n) for n in names}
    adp_order = derive_adp_order(store, season)

    for rot in range(rotations):
        seats: list = [None] * n_teams
        seat_of: dict[str, int] = {}
        for i, name in enumerate(names):
            seat = (i + rot) % n_teams
            seats[seat] = strategies[name]
            seat_of[name] = seat
        for s in range(n_teams):
            if seats[s] is None:
                seats[s] = bot_strategy(AdpBot(adp_order, random.Random(seed + rot * 100 + s)))

        rosters = snake_draft(seats, pool, settings)
        graded = score_rosters(store, season, rosters, settings, schedule=schedule)

        for name in names:
            seat = seat_of[name]
            g = graded[seat]
            r = results[name]
            r.category_wins += g["cat_wins"]
            r.category_games += g["cat_games"]
            r.matchup_wins += g["matchup_wins"]
            r.matchups += g["matchups"]
            r.by_seat[seat][0] += g["cat_wins"]
            r.by_seat[seat][1] += g["cat_games"]
            for c, (w, n) in g["per_cat"].items():
                r.per_category[c][0] += w
                r.per_category[c][1] += n
    return results


def format_replay(results: dict[str, StrategyResult]) -> str:
    width = max([10, *(len(n) for n in results)])
    lines = [f"{'strategy':<{width}} {'cat win%':>10} {'matchup%':>10} {'n':>8}"]
    for name, r in sorted(results.items(), key=lambda kv: -kv[1].category_win_rate):
        lines.append(
            f"{name:<{width}} {100 * r.category_win_rate:>9.1f}% "
            f"{100 * r.matchup_win_rate:>9.1f}% {r.category_games:>8d}"
        )
    return "\n".join(lines)
