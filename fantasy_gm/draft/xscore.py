"""The variance-aware standardisation basis, and its static reduction (G-score).

**Why z-score is wrong here.** Standard 9-cat value standardises a player's per-game mean by
the *player-to-player* spread of that mean: ``(μ_p − μ̄) / σ``. That answers "how unusual is
this player?" — which is the right question for a season-long ranking and the wrong one for
weekly H2H. In H2H you win a category by beating one opponent over a handful of games, so the
week-to-week fluctuation of the output matters as much as its level. Z-score is the special
case of a more general metric under the assumption that future production is *known exactly*
(Rosenof, arXiv 2307.02188); that assumption is what fails.

**The correction.** Add the period-to-period variance to the denominator::

    x_pc = direction_c · (μ_pc − μ̄_c) / sqrt(σ_c² + κ · τ_c²)

where, per category ``c`` measured over scoring periods (weeks):

* ``μ_pc`` — player ``p``'s mean weekly total
* ``μ̄_c``, ``σ_c`` — mean and spread of ``μ_pc`` *across the pool* (the z-score denominator)
* ``τ_c``  — the week-to-week spread of a player's own weekly totals
* ``κ``    — how heavily period noise counts relative to player spread

Summing ``x_pc`` over categories gives **G-score**, a complete static draft board and the
fallback if the dynamic optimizer is not ready. It is also the basis the H₀ optimizer scores
candidates in, so it is implemented once here.

**Two things this module does that the paper does not.**

1. ``τ`` is *measured per player* (:attr:`VarianceMode.MEASURED`), not assumed uniform across
   the league. Uniform ``τ`` is the paper's own first stated limitation; it is retained as
   :attr:`VarianceMode.UNIFORM` so the two can be compared in replay rather than argued about
   (assumptions ledger A-DRAFT-1).
2. Periods are built by aggregating real game logs into calendar weeks, so ``τ`` includes
   **weeks the player did not play**. Those zero-weeks are not noise to be cleaned out — they
   are the single largest source of realized variance in a category league, and modelling them
   away is what produced the 31%-of-adds-never-played failure in season replay.

Percentage categories follow the store's volume-weighted convention (A8): a week contributes
``(week_pct − league_pct) × week_attempts``, never a bare percentage, so a 3-attempt week
cannot outweigh a 20-attempt one.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from statistics import fmean, median, pstdev

from fantasy_gm.config import CATEGORY_DIRECTION, DEFAULT_CATEGORIES, PERCENTAGE_CATEGORIES
from fantasy_gm.valuation import rosterable_pool

# κ weights period-to-period noise against player-to-player spread. PROVISIONAL: 1.0 gives
# the two equal weight, which is a choice, not a measurement. `kappa_sensitivity` reports how
# much the board actually moves across a range — if it is flat, stop tuning and say so
# (assumptions ledger A-DRAFT-4).
DEFAULT_KAPPA = 1.0


class VarianceMode(StrEnum):
    """Where a player's period-to-period variance comes from."""

    MEASURED = "measured"  # the player's own weekly spread (default)
    UNIFORM = "uniform"    # one league-typical τ for everyone — the paper's assumption


@dataclass(frozen=True)
class PeriodStats:
    """One player's weekly production in one category."""

    mean: float          # μ_pc — mean weekly total
    std: float           # τ_pc — spread of that player's weekly totals
    periods: int         # weeks observed


@dataclass(frozen=True)
class CategoryBasis:
    """Pool-level standardisation constants for one category."""

    category: str
    pool_mean: float      # μ̄_c
    pool_std: float       # σ_c — player-to-player spread
    typical_tau: float    # τ̄_c — league-typical week-to-week spread
    league_rate: float = 0.0  # percentage categories only: pooled makes/attempts

    def denominator(self, tau: float, kappa: float) -> float:
        """sqrt(σ² + κ·τ²), floored so a degenerate category cannot divide by zero."""
        return max((self.pool_std**2 + kappa * tau**2) ** 0.5, 1e-9)


@dataclass
class XScoreBasis:
    """Everything needed to standardise any player in any category."""

    categories: list[str]
    bases: dict[str, CategoryBasis]
    stats: dict[str, dict[str, PeriodStats]]  # player -> category -> PeriodStats
    pool: list[str]
    kappa: float = DEFAULT_KAPPA
    mode: VarianceMode = VarianceMode.MEASURED
    notes: dict[str, str] = field(default_factory=dict)

    def tau_for(self, player_id: str, category: str) -> float:
        basis = self.bases[category]
        if self.mode is VarianceMode.UNIFORM:
            return basis.typical_tau
        ps = self.stats.get(player_id, {}).get(category)
        return ps.std if ps is not None else basis.typical_tau

    def category_score(self, player_id: str, category: str) -> float:
        """Standardised contribution of one player in one category."""
        basis = self.bases[category]
        ps = self.stats.get(player_id, {}).get(category)
        if ps is None:
            return 0.0
        denom = basis.denominator(self.tau_for(player_id, category), self.kappa)
        direction = 1 if category in PERCENTAGE_CATEGORIES else CATEGORY_DIRECTION[category]
        return direction * (ps.mean - basis.pool_mean) / denom

    def total(self, player_id: str) -> float:
        return sum(self.category_score(player_id, c) for c in self.categories)


def _iso_week(day: str) -> str:
    y, w, _ = date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def _weekly_lines(store, season: str) -> dict[str, dict[str, list[dict]]]:
    """``player -> week -> [box lines]`` for every game played in ``season``."""
    out: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in store.conn.execute(
        "SELECT player_id, game_date, stats_json FROM player_logs WHERE season = ?", (season,)
    ):
        out[r["player_id"]][_iso_week(r["game_date"])].append(json.loads(r["stats_json"]))
    return out


def measure_period_stats(
    store,
    season: str,
    categories: list[str] | None = None,
    pool_size: int = 156,
    include_idle_weeks: bool = True,
) -> tuple[dict[str, dict[str, PeriodStats]], list[str]]:
    """Measure each pooled player's weekly mean and spread per category.

    ``include_idle_weeks`` fills weeks between a player's first and last appearance in which
    they did not play with a zero total. Those weeks are real — a rostered player who does not
    take the floor scores nothing — and excluding them understates τ badly for exactly the
    injury-prone and rotation-fringe players a draft most needs to price. Weeks outside the
    player's active span are not invented, since they usually mean "not yet in the league" or
    "season over" rather than "sat out".
    """
    categories = list(categories or DEFAULT_CATEGORIES)
    counting = [c for c in categories if c not in PERCENTAGE_CATEGORIES]
    pcts = [c for c in categories if c in PERCENTAGE_CATEGORIES]
    weekly = _weekly_lines(store, season)
    pool = rosterable_pool(store, season, pool_size=pool_size)

    # Pooled league rate per percentage category, over the pool (volume-weighted, A8).
    league_rates: dict[str, float] = {}
    for c in pcts:
        mk, at = PERCENTAGE_CATEGORIES[c]
        made = att = 0.0
        for pid in pool:
            for lines in weekly.get(pid, {}).values():
                made += sum(line.get(mk, 0.0) for line in lines)
                att += sum(line.get(at, 0.0) for line in lines)
        league_rates[c] = made / att if att > 0 else 0.0

    all_weeks = sorted({w for weeks in weekly.values() for w in weeks})
    stats: dict[str, dict[str, PeriodStats]] = {}
    for pid in pool:
        weeks = weekly.get(pid, {})
        if not weeks:
            continue
        active = sorted(weeks)
        span = (
            [w for w in all_weeks if active[0] <= w <= active[-1]]
            if include_idle_weeks
            else active
        )
        per_cat: dict[str, PeriodStats] = {}
        for c in counting:
            totals = [sum(line.get(c, 0.0) for line in weeks.get(w, [])) for w in span]
            per_cat[c] = _summarise(totals)
        for c in pcts:
            mk, at = PERCENTAGE_CATEGORIES[c]
            rate = league_rates[c]
            impacts = []
            for w in span:
                lines = weeks.get(w, [])
                made = sum(line.get(mk, 0.0) for line in lines)
                att = sum(line.get(at, 0.0) for line in lines)
                # A week with no attempts has zero impact — not an undefined percentage.
                impacts.append((made / att - rate) * att if att > 0 else 0.0)
            per_cat[c] = _summarise(impacts)
        stats[pid] = per_cat
    return stats, pool


def _summarise(totals: list[float]) -> PeriodStats:
    if not totals:
        return PeriodStats(0.0, 0.0, 0)
    if len(totals) == 1:
        return PeriodStats(totals[0], 0.0, 1)
    return PeriodStats(fmean(totals), pstdev(totals), len(totals))


def xscore_basis(
    store,
    season: str,
    categories: list[str] | None = None,
    pool_size: int = 156,
    kappa: float = DEFAULT_KAPPA,
    mode: VarianceMode = VarianceMode.MEASURED,
    include_idle_weeks: bool = True,
) -> XScoreBasis:
    """Build the standardisation basis for ``season`` over the rosterable pool.

    ``include_idle_weeks`` defaults to True and **must stay that way for replay**: a week the
    player missed is a week the manager lost the category, and modelling those away is what
    produced the 31%-of-adds-never-played failure. It is exposed only so a *forward-looking*
    board can separate the two effects — see :mod:`fantasy_gm.draft.board`, which found that
    realized availability accounts for most of the board's disagreement with z-score and is
    hindsight when the board is published before a season rather than scored after one.
    """
    categories = list(categories or DEFAULT_CATEGORIES)
    stats, pool = measure_period_stats(
        store, season, categories, pool_size, include_idle_weeks=include_idle_weeks
    )
    scored = [p for p in pool if p in stats]

    bases: dict[str, CategoryBasis] = {}
    for c in categories:
        means = [stats[p][c].mean for p in scored]
        taus = [stats[p][c].std for p in scored]
        bases[c] = CategoryBasis(
            category=c,
            pool_mean=fmean(means) if means else 0.0,
            pool_std=(pstdev(means) if len(means) > 1 else 0.0) or 1e-9,
            # Median, not mean: τ is right-skewed by a handful of erratic high-usage
            # players, and the uniform mode is meant to represent a typical player.
            typical_tau=median(taus) if taus else 0.0,
        )
    return XScoreBasis(
        categories=categories, bases=bases, stats=stats, pool=scored, kappa=kappa, mode=mode
    )


def g_scores(
    store,
    season: str,
    categories: list[str] | None = None,
    pool_size: int = 156,
    kappa: float = DEFAULT_KAPPA,
    mode: VarianceMode = VarianceMode.MEASURED,
) -> dict[str, float]:
    """``{player_id: G-score}`` — the static, variance-aware draft value."""
    basis = xscore_basis(store, season, categories, pool_size, kappa, mode)
    return {p: round(basis.total(p), 4) for p in basis.pool}


def g_score_board(
    store,
    season: str,
    categories: list[str] | None = None,
    pool_size: int = 156,
    kappa: float = DEFAULT_KAPPA,
    mode: VarianceMode = VarianceMode.MEASURED,
    limit: int | None = None,
    include_idle_weeks: bool = True,
) -> list[tuple[str, float, dict[str, float]]]:
    """Ranked board: ``[(player_id, total, {category: contribution})]``, best first.

    The per-category breakdown is what makes a pick explainable — and what the H₀ optimizer
    consumes when it decides which categories a roster is already winning.
    """
    basis = xscore_basis(store, season, categories, pool_size, kappa, mode, include_idle_weeks)
    rows = [
        (p, round(basis.total(p), 4), {c: round(basis.category_score(p, c), 4)
                                       for c in basis.categories})
        for p in basis.pool
    ]
    rows.sort(key=lambda r: -r[1])
    return rows[:limit] if limit else rows


def kappa_sensitivity(
    store,
    season: str,
    kappas: list[float] | None = None,
    categories: list[str] | None = None,
    pool_size: int = 156,
    top_n: int = 50,
) -> list[tuple[float, int, float]]:
    """How much κ actually moves the board: ``[(κ, rank_changes_in_top_n, max_rank_shift)]``.

    Measured against κ=0 (which reduces to a period-aggregated z-score). If the board barely
    moves across a plausible range, κ is not worth tuning and that should be recorded rather
    than quietly fitted (A-DRAFT-4).
    """
    kappas = kappas or [0.0, 0.5, 1.0, 2.0, 4.0]
    baseline = [p for p, _, _ in g_score_board(store, season, categories, pool_size, 0.0)]
    base_rank = {p: i for i, p in enumerate(baseline)}
    out: list[tuple[float, int, float]] = []
    for k in kappas:
        board = [p for p, _, _ in g_score_board(store, season, categories, pool_size, k)]
        changed = sum(1 for i, p in enumerate(board[:top_n]) if base_rank.get(p) != i)
        shift = max(
            (abs(base_rank.get(p, i) - i) for i, p in enumerate(board[:top_n])), default=0
        )
        out.append((k, changed, float(shift)))
    return out


def measure_per_game_stats(
    store,
    season: str,
    categories: list[str] | None = None,
    pool_size: int = 156,
) -> tuple[dict[str, dict[str, PeriodStats]], list[str]]:
    """Per-**game** mean and spread per category, over the rosterable pool.

    The counterpart to :func:`measure_period_stats`, and the input a *forward* board has to be
    built from. Aggregating straight to weekly totals — even over active weeks only — silently
    keeps each player's realized games *per active week*, because a week in which someone
    suited up twice instead of four times still counts, at a lower total. That factor
    correlates ~+0.75 with realized games played and is measured on the season being graded, so
    a board built on it ranks up the players who turned out to stay healthy no matter what
    availability projection it was handed. Measured on 2024-25 and 2025-26, a `projected` board
    built the old way correlated **+0.60/+0.64 with realized games and ~0.00 with the projected
    games it was actually given** (A-DRAFT-14).

    ``PeriodStats.periods`` here counts games, not weeks. Compounding these up to a week is
    :func:`fantasy_gm.draft.board.compound_weekly`'s job, and it uses a *scheduled* game count
    so no realized availability can re-enter.
    """
    categories = list(categories or DEFAULT_CATEGORIES)
    counting = [c for c in categories if c not in PERCENTAGE_CATEGORIES]
    pcts = [c for c in categories if c in PERCENTAGE_CATEGORIES]

    lines: dict[str, list[dict]] = defaultdict(list)
    for r in store.conn.execute(
        "SELECT player_id, stats_json FROM player_logs WHERE season = ?", (season,)
    ):
        lines[r["player_id"]].append(json.loads(r["stats_json"]))
    pool = rosterable_pool(store, season, pool_size=pool_size)

    league_rates: dict[str, float] = {}
    for c in pcts:
        mk, at = PERCENTAGE_CATEGORIES[c]
        made = sum(g.get(mk, 0.0) for p in pool for g in lines.get(p, ()))
        att = sum(g.get(at, 0.0) for p in pool for g in lines.get(p, ()))
        league_rates[c] = made / att if att > 0 else 0.0

    stats: dict[str, dict[str, PeriodStats]] = {}
    for pid in pool:
        games = lines.get(pid)
        if not games:
            continue
        per_cat: dict[str, PeriodStats] = {}
        for c in counting:
            per_cat[c] = _summarise([g.get(c, 0.0) for g in games])
        for c in pcts:
            mk, at = PERCENTAGE_CATEGORIES[c]
            rate = league_rates[c]
            per_cat[c] = _summarise([
                (g.get(mk, 0.0) / g.get(at, 0.0) - rate) * g.get(at, 0.0)
                if g.get(at, 0.0) > 0 else 0.0
                for g in games
            ])
        stats[pid] = per_cat
    return stats, pool


def scheduled_games_per_week(store, season: str) -> float:
    """League-mean **scheduled** games per team per week, from the published schedule.

    A season's schedule is public months before a draft, so this carries no lookahead — unlike
    counting the games a player actually appeared in, which is the whole problem this replaces.
    Team-to-team variation over a full season is negligible (every team plays 82 games across
    the same calendar), so one league constant is used rather than a per-team figure that would
    invite reading it from the graded season's box scores.
    """
    rows = list(store.conn.execute(
        "SELECT game_date, home_team, away_team FROM games WHERE season = ?", (season,)
    ))
    if not rows:
        return 3.5
    weeks: dict[str, set[str]] = defaultdict(set)
    team_games: dict[str, int] = defaultdict(int)
    for r in rows:
        w = _iso_week(r["game_date"])
        for team in (r["home_team"], r["away_team"]):
            weeks[team].add(w)
            team_games[team] += 1
    per_team = [team_games[t] / len(weeks[t]) for t in team_games if weeks[t]]
    return fmean(per_team) if per_team else 3.5
