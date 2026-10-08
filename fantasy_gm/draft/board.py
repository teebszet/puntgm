"""The static G-score draft board — the free, shippable surface.

**Why this exists.** It is the only draft surface that needs no OAuth, no forward category
projection and no working optimizer, so it is what can face traffic inside the mid-September
window. It was written when H₀ was losing to it on three seasons of replay; task 3.8 has since
shown that comparison was measuring a broken build of H₀ (A-DRAFT-17), so the board is once
again the *first* product rather than the better one. That does not change what it is or what
it may claim.

**What it ranks on.** ``g_score_board`` sums the variance-aware basis over the scored
categories. That basis is the whole claim: z-score standardises by player-to-player spread
alone, which is the right unit for a season-long ranking and the wrong one for a category
decided over one week (Rosenof, arXiv 2307.02188).

**That claim did not survive being measured, and what replaced it is the one to publish
(A-DRAFT-15).** Seat-mirrored across three seasons and four seeds, this board beats the
**per-game** z-score that every free ranking list publishes by **+11 to +20pp** of category win
rate, in twelve runs out of twelve. Against a **total-value** z-score — z computed on season
totals, which Basketball Monster and Hashtag both expose behind a toggle — it *loses*, by 0.7
to 3.8pp, also twelve out of twelve. At the fitted ``BOARD_KAPPA = 0`` the two are the same
board (max rank delta 0 over 156 players), because a leak-free forward board is per-game mean ×
projected rate and that is exactly what total-value z computes.

So the edge is **availability, not variance**, and it is an edge over the *free* rankings only.
An earlier "+5 to +9pp forward-honest" figure is **withdrawn**: the boards it was measured on
leaked realized games-per-week (A-DRAFT-14) and the harness had a seat-adjacency bias
(A-DRAFT-16). The +13.4pp figure in `results.md` is the *realized-availability* arm and is not
reachable by anyone drafting in advance.

**Punting is declared here, not emergent.** In H₀ concentration falls out of the optimisation
and is never named. A static board cannot do that, so a punt build is exactly what the market's
punt checkboxes are: the punted categories are dropped from the scored set and the board is
re-ranked over what remains. Stated plainly rather than dressed up — the honest claim is "the
same punt checkbox everyone ships, computed in a better metric", and the z-score delta column
is what makes that difference visible.

**Availability is separated from variance, deliberately (A-DRAFT-14).** Measured on 2025-26,
`corr(games played, rank change vs z-score) = +0.627` — most of the board's disagreement with
the market metric was not the variance correction at all, it was that G-score counts weeks the
player missed as zeros and z-score is availability-blind. Counting those zeros is *correct for
replay*, where the season already happened and a missed week really did lose the category. It
is **hindsight for a board published before a draft**: it ranks Giannis 118th because he missed
46 games last season. So the board measures production per game and reintroduces
availability as a separately projected, shrunk term (:mod:`fantasy_gm.projections.availability`,
the A13 model that is the in-season engine's single biggest win). Two claims, two columns, each
auditable on its own. See :class:`AvailabilityMode`.

**Provenance is part of the artifact.** Every board carries a :attr:`Board.basis` line saying
what it was measured from and how availability was handled. It is not a category-level forward
projection, and publishing it as one would be the exact failure the assumptions ledger exists
to prevent (A-DRAFT-5's gate on the projection backtest is still open). :func:`board_json` and
:func:`render_markdown` both emit that line, so it cannot be dropped by the rendering layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum

from fantasy_gm.config import DEFAULT_CATEGORIES, PERCENTAGE_CATEGORIES
from fantasy_gm.draft.xscore import (
    CategoryBasis,
    PeriodStats,
    VarianceMode,
    XScoreBasis,
    league_percentage_rates,
    measure_per_game_stats,
    measure_period_stats,
    scheduled_games_per_week,
)
from fantasy_gm.models import Availability
from fantasy_gm.projections.status import ACTIVE, rate_factor, status_for_pool
from fantasy_gm.valuation import player_values

# The games-played floor a baseline season must clear to price a player the ranked season
# cannot (R3) — the same floor rosterable_pool uses for eligibility.
BASELINE_MIN_GAMES = 10

# The rate bases a board can rank on (R4). ``measured`` — the default — reads ranked-season
# game logs (with last-healthy baselines); ``projected`` hands the per-game rates to the
# derived minutes/role model, which carries each player's role onto his forward roster.
RATE_BASES = ("measured", "projected")


class AvailabilityMode(StrEnum):
    """How the board accounts for a player being on the floor.

    * :attr:`REALIZED` — weeks the player missed count as zero-production weeks. This is what
      the replay harness uses and what produced the published +13.4pp result. Correct when
      grading a season that already happened; hindsight when ranking one that has not.
    * :attr:`NEUTRAL` — only weeks the player actually played. Isolates the variance claim from
      the availability claim. Ranks a 30-game star as if durable, which is its own distortion —
      offered as the ablation, not as the product.
    * :attr:`PROJECTED` — neutral production, then availability reintroduced as a *forward*
      beta-binomial projection shrunk toward the pool rate. The default, and the only one of
      the three that is defensible on a board published before a draft.
    """

    REALIZED = "realized"
    NEUTRAL = "neutral"
    PROJECTED = "projected"


class RateBasis(StrEnum):
    """Where a board's per-game rates come from (R4/D3).

    * :attr:`MEASURED` — the ranked season's game logs, with last-healthy baselines behind
      them (R3). The default: every number on the board was measured, nothing modeled.
    * :attr:`PROJECTED` — the derived minutes/role model carries each player's usage onto
      his forward-season roster, so a team change the ranked season cannot see moves the
      rate. Opt-in; pool and standardisation stay measured either way.
    """

    MEASURED = "measured"
    PROJECTED = "projected"


# Named builds, chosen for what a 9-cat drafter actually plays rather than for coverage of the
# combinatorial space. `punt_ft` and `punt_tov` are the two the measured findings support most
# directly: ft_pct is the category the in-season engine gates out as non-actionable (A15), and
# both are categories where a strong player is routinely dragged down by one number.
# κ for a published board, MEASURED (A-DRAFT-4) rather than the provisional 1.0 the engine
# still carries. κ weights period-to-period variance in the standardisation denominator, and
# it is the entire thesis of the G-score correction. Swept over {0, 0.25, 0.5, 1, 2, 4} in a
# seat-mirrored draft replay across two seasons and four seeds, in both a forward-honest and a
# hindsight pairing, **κ=0 won every single run** and the decline was monotone in κ. The
# variance correction is not under-tuned here; on this data it is harmful.
#
# Deliberately *not* applied to `xscore.DEFAULT_KAPPA`, which the H₀ engine reads. H₀ scores
# candidates through a Poisson-binomial over category wins, where the variance term does
# different work, and task 3.8 is still open on whether that implementation reproduces the
# paper at all. Moving both at once would confound that investigation with this one.
BOARD_KAPPA = 0.0

PUNT_BUILDS: dict[str, tuple[str, ...]] = {
    "balanced": (),
    "punt_ft": ("ft_pct",),
    "punt_fg": ("fg_pct",),
    "punt_tov": ("tov",),
    "punt_ast": ("ast",),
    "punt_pts": ("pts",),
    "punt_ft_tov": ("ft_pct", "tov"),
    "punt_fg_ft": ("fg_pct", "ft_pct"),
    "punt_ast_tov": ("ast", "tov"),
}


@dataclass(frozen=True)
class BoardRow:
    """One player's line on a board, with the z-score comparison that makes it interesting."""

    rank: int
    player_id: str
    player_name: str
    total: float
    categories: dict[str, float]
    expected_games: float | None = None
    availability_rate: float | None = None
    z_rank: int | None = None
    z_delta: int | None = None
    """``z_rank - rank``. Positive means G-score rates the player *higher* than z-score does —
    i.e. the market, which runs on z-score, is underrating them. Negative is the reverse. This
    column is the difference between the two metrics made legible, and it is the content."""
    status: str | None = None
    """Platform designation at the board date (ACTIVE | QUESTIONABLE | OUT), if any. Only the
    ``projected`` availability mode consumes status, so other modes carry ``None``."""
    status_note: str | None = None
    """The dated injury note Yahoo published with the designation — display context only."""
    rate_source: str | None = None
    """Where this row's per-game rates came from when not the ranked season
    (e.g. ``"baseline:2024-25"``); ``None`` means ranked-season rates."""


@dataclass(frozen=True)
class Board:
    """A complete ranked board for one build, carrying its own provenance."""

    season: str
    build: str
    punt: tuple[str, ...]
    categories: tuple[str, ...]
    pool_size: int
    kappa: float
    variance_mode: str
    availability: AvailabilityMode = AvailabilityMode.PROJECTED
    availability_as_of: str | None = None
    forward_season: str | None = None
    rate_basis: RateBasis = RateBasis.MEASURED
    """The per-game rate source, per R4/D3 (measured default; projected opt-in)."""
    projected_as_of: str | None = None
    """The date projected rates were measured on — set only under ``projected``."""
    status_as_of: str | None = None
    """The date platform status was read at — set only when the mode consumes status."""
    status_counts: dict[str, int] = field(default_factory=dict)
    """Designations carried by the pool at ``status_as_of``, by status (non-ACTIVE only)."""
    rate_sources: dict[str, str] = field(default_factory=dict)
    """Per-player rate provenance for everyone priced outside the ranked season."""
    unpriced: tuple[tuple[str, str, str], ...] = ()
    """``(player_id, name, reason)`` for every pool player nothing could price."""
    rows: list[BoardRow] = field(default_factory=list)

    @property
    def basis(self) -> str:
        """The provenance line. Published output must carry this verbatim."""
        head = (
            f"Per-game production measured from {self.season} game logs over games actually "
            "played — DNP rows enter the availability term, not the rates — for the top "
            f"{self.pool_size} players by {self.season} minutes per game"
            + (
                f", players without a usable {self.season} sample placed by depth on their "
                f"{self.forward_season} roster"
                if self.forward_season else ""
            )
            + (
                (
                    f", {len(self.rate_sources)} of those priced per-game from their last "
                    "healthy season (named per row)"
                    if self.rate_basis == RateBasis.MEASURED
                    else
                    f", {len(self.rate_sources)} priced per-game outside the ranked season "
                    "by the derived model or a baseline (named per row)"
                )
                if self.rate_sources else ""
            )
            + "."
        )
        projected_rates = (
            ""
            if self.rate_basis == RateBasis.MEASURED
            else
            " Per-game rates are the derived minutes/role model's projection as of "
            f"{self.projected_as_of}, carried onto each player's forward-season roster — a "
            "model whose minutes edge over naive carry-forward is unproven (backtest "
            "inconclusive)."
        )
        if self.availability is AvailabilityMode.REALIZED:
            return (
                f"{head} Weeks missed count as zero-production weeks, so last season's "
                "realized availability is baked into the rank — replay-faithful, but "
                "hindsight for a board published before a draft."
            )
        if self.availability is AvailabilityMode.NEUTRAL:
            return (
                f"{head} Compounded to a week over the league's scheduled games per week with "
                "no availability term at all, so a player who missed half the season is ranked "
                f"as if durable. Ablation, not a recommendation.{projected_rates}"
            )
        status = ""
        if self.status_as_of:
            if self.status_counts:
                flagged = ", ".join(f"{s} {n}" for s, n in sorted(self.status_counts.items()))
                status = (
                    f" Platform status (yahoo, as of {self.status_as_of}) caps the "
                    "availability rate where a designation exists — OUT ×0.5 (season-ending "
                    f"×0), QUESTIONABLE ×0.75, never a raise ({flagged})."
                )
            else:
                status = (
                    f" Platform status (yahoo, as of {self.status_as_of}) carried no "
                    "designation when this board was built."
                )
        rates = (
            "Category rates are measured, not projected forward."
            if self.rate_basis == RateBasis.MEASURED
            else ""
        )
        return (
            f"{head} Compounded to a week over the league's scheduled games per week, each "
            "played with an expected-availability probability projected as of "
            f"{self.availability_as_of} — a beta-binomial rate shrunk toward the pool, not "
            "last season's realized games. No count of the games this player actually went on "
            f"to play enters the rank. {rates}{projected_rates}"
            + status
        )

    @property
    def label(self) -> str:
        return "Balanced" if not self.punt else "Punt " + " + ".join(self.punt)


def _player_names(store, season: str, player_ids: list[str]) -> dict[str, str]:
    """Resolve display names in one query rather than one per row."""
    if not player_ids:
        return {}
    marks = ",".join("?" * len(player_ids))
    rows = store.conn.execute(
        f"SELECT player_id, player_name, MAX(game_date) FROM player_logs "  # noqa: S608
        f"WHERE season = ? AND player_id IN ({marks}) GROUP BY player_id",
        (season, *player_ids),
    ).fetchall()
    return {r["player_id"]: r["player_name"] for r in rows}


def project_availability(
    store, season: str, as_of: str, players: list[str] | None = None,
    statuses: dict[str, Availability | None] | None = None,
) -> dict[str, object]:
    """``{player_id: GamesProjection}`` from history known at ``as_of``.

    Reads through :mod:`fantasy_gm.projections.availability` rather than counting last
    season's games, so the rate is shrunk toward the pool by a fitted prior and a player with
    one unlucky season is not condemned by it.

    ``players`` names everyone the board needs a rate for. Anyone in it with no games before
    ``as_of`` — a rookie, or a returnee the store has not seen — is projected from ``(0, 0)``,
    which the beta-binomial resolves to the fitted **pool rate**. Leaving them out instead
    would silently hand them a rate of 1.0, i.e. rank a player who has never appeared in the
    league as a nailed-on 82-game starter. That is not a conservative default; it put two
    rookies in the top eight of the first board this produced.

    Platform status (task 2.1) then caps the projected rate where a designation exists:
    OUT season-ending → 0, other OUT → ×0.5, QUESTIONABLE → ×0.75 (see
    :mod:`fantasy_gm.projections.status`). A designation never *raises* the rate — an ACTIVE
    row is a statement about tonight, and the beta-binomial already prices recovery
    conservatively — so an empty designation table changes nothing. ``statuses`` may be
    passed by a caller that already read them (the board reads once for both pricing and
    display); otherwise they are read here, so every consumer of this function prices the
    same world whichever metric ranks them afterwards.
    """
    from fantasy_gm.projections.availability import GamesModel, fit_games

    fit = fit_games(store, as_of)
    model = GamesModel(fit)
    per_player: dict[str, list[dict]] = {}
    for row in store.player_game_stream_asof(as_of):
        per_player.setdefault(row["player_id"], []).append(row)

    out: dict[str, object] = {}
    for pid, games in per_player.items():
        team = games[-1]["team"]
        team_games = store.games_in_window_for_team(team, games[0]["game_date"], as_of)
        # Observed games are games actually played: a DNP row is a game NOT played, so it
        # must not inflate the numerator the beta-binomial shrinks (R1/D1). The row still
        # sits in the stream and in the team-games denominator — it is carried by this
        # availability term, exactly as the rate side excludes it.
        observed = sum(1 for g in games if g["minutes"] is None or g["minutes"] > 0)
        out[pid] = model.project(pid, observed, team_games)
    for pid in players or []:
        if pid not in out:
            out[pid] = model.project(pid, 0, 0)

    if statuses is None:
        statuses = status_for_pool(store, list(out), as_of)
    for pid, avail in statuses.items():
        if avail is None or avail.status == ACTIVE:
            continue
        g = out.get(pid)
        if g is None:
            continue
        factor = rate_factor(avail)
        if factor == 1.0:
            continue
        out[pid] = _capped(g, factor)
    return out


def _projected_per_game(
    store,
    pool: list[str],
    categories: list[str],
    season: str,
    forward_season: str,
    as_of: str,
) -> dict[str, tuple[str, dict[str, PeriodStats]]]:
    """Per-game category stats from the derived minutes/role model (R4/D3).

    The model projects each pool player's forward-season per-game lines onto the roster
    his ``forward_season`` record carries — team, stated depth, offseason moves — which is
    exactly the "rosters changed" term a measured board cannot see. The fit reads nothing
    after ``as_of`` by construction (:mod:`fantasy_gm.projections.derived`).

    Percentage categories are never projected directly (A8): the source emits makes and
    attempts, and the board's impact form ``(rate − league%) × attempts`` is taken against
    the ranked season's pooled league rate — the same single environment baselines are
    measured against, so rows stay comparable across rate sources. Component spread
    propagates as ``sqrt(std_make² + rate²·std_att²)``, cross-term deliberately ignored
    (makes and attempts are strongly positively correlated, so this *widens* the band —
    conservative, and documented rather than hidden).

    Returns ``{player_id: (provenance tag, per-cat stats)}`` for every pool player the
    model prices — ``projected:<as_of>`` for a modeled line, ``prior:<as_of>`` for a
    rookie prior, ``override:<as_of>`` for a hand-set one. Players the model cannot price
    (no history, not incoming) are simply absent: their measured or baseline rates stand,
    and provenance keeps saying so.
    """
    from fantasy_gm.projections.derived import DerivedProjectionSource
    from fantasy_gm.projections.source import ProjectionBasis

    league = league_percentage_rates(store, season, categories, pool)
    projections = DerivedProjectionSource(store, categories=categories).project(
        forward_season, as_of, player_ids=pool,
    )
    counting = [c for c in categories if c not in PERCENTAGE_CATEGORIES]
    pcts = [c for c in categories if c in PERCENTAGE_CATEGORIES]
    tag_of = {
        ProjectionBasis.MODELED: "projected",
        ProjectionBasis.PRIOR: "prior",
        ProjectionBasis.OVERRIDE: "override",
    }
    out: dict[str, tuple[str, dict[str, PeriodStats]]] = {}
    for pid, p in projections.items():
        per_cat: dict[str, PeriodStats] = {}
        for c in counting:
            e = p.estimates.get(c)
            if e is None:
                per_cat = {}
                break
            per_cat[c] = PeriodStats(e.per_game_mean, e.per_game_std, periods=0)
        if per_cat:
            for c in pcts:
                mk, at = PERCENTAGE_CATEGORIES[c]
                m, a = p.estimates.get(mk), p.estimates.get(at)
                if m is None or a is None:
                    per_cat = {}
                    break
                rate = league.get(c, 0.0)
                mean = m.per_game_mean - rate * a.per_game_mean
                std = (m.per_game_std**2 + (rate * a.per_game_std) ** 2) ** 0.5
                per_cat[c] = PeriodStats(mean, std, periods=0)
        if per_cat:
            out[pid] = (f"{tag_of.get(p.basis, 'projected')}:{as_of}", per_cat)
    return out


def _capped(g, factor: float):
    """A rate-capped copy of a GamesProjection.

    Expected games scale linearly with the rate; the spread is scaled by the same factor as
    an approximation — the board's compounding reads the rate alone, so nothing downstream
    consumes the scaled std.
    """
    from fantasy_gm.projections.availability import GamesProjection

    return GamesProjection(
        player_id=g.player_id,
        expected_games=g.expected_games * factor,
        expected_games_std=g.expected_games_std * factor,
        availability_rate=g.availability_rate * factor,
        observed_games=g.observed_games,
        team_games=g.team_games,
    )


def compound_weekly(
    per_game: dict[str, dict[str, PeriodStats]],
    rates: dict[str, float],
    games_per_week: float,
) -> dict[str, dict[str, PeriodStats]]:
    """Build a weekly total from per-game stats and a projected availability rate.

    A week is ``n`` scheduled games, each played with probability ``r`` and contributing a
    per-game mean ``m`` and variance ``v``. Compounding gives::

        mu'   = n · r · m
        tau'2 = n · r · v  +  n · r(1 - r) · m2

    Two properties this construction has and the previous one did not. First, availability
    enters **only** through ``r``, so a forward board cannot rank a player up for having turned
    out to stay healthy — the old path measured weekly totals over active weeks, which quietly
    retained each player's realized games *per week* (A-DRAFT-14, second leak). Second, ``n`` is
    the *scheduled* game count, identical for everyone, so nothing about the graded season
    reaches it.

    Availability is binomial over games, never Bernoulli over weeks: players miss individual
    nights. Dropping the game count inflates the variance penalty by n ~ 3.3, and since that
    term scales with ``m2`` the error lands almost entirely on high-production players — the
    first version of this ranked durable role players above every star. Setting ``r = 1``
    recovers the pure sum-of-n-games case, which is the neutral ablation.
    """
    n = max(games_per_week, 1.0)
    out: dict[str, dict[str, PeriodStats]] = {}
    for pid, by_cat in per_game.items():
        r = min(max(rates.get(pid, 1.0), 0.0), 1.0)
        out[pid] = {
            c: PeriodStats(
                mean=n * r * ps.mean,
                std=(n * r * ps.std**2 + n * r * (1.0 - r) * ps.mean**2) ** 0.5,
                periods=ps.periods,
            )
            for c, ps in by_cat.items()
        }
    return out


def _basis(
    store,
    season: str,
    categories: list[str],
    pool_size: int,
    kappa: float,
    mode: VarianceMode,
    availability: AvailabilityMode,
    as_of: str | None,
    forward_season: str | None = None,
    rate_basis: str = "measured",
) -> tuple[XScoreBasis, dict[str, object], dict[str, Availability | None],
           dict[str, str], list[str]]:
    """Build the standardisation basis under one availability treatment.

    Returns ``(basis, projections, statuses, rate_sources, pool)``. ``statuses`` is the
    effective-dated platform status the board consumed — empty outside ``projected``, which
    is the only mode that prices availability (``realized`` grades a finished season,
    ``neutral`` is the no-availability ablation). ``rate_sources`` names every player priced
    outside the ranked season — ``baseline:<season>`` for a last-healthy baseline, or
    ``projected:``/``prior:``/``override:`` + the projection date under the projected rate
    basis (R4).
    """
    from statistics import fmean, median, pstdev

    projections: dict[str, object] = {}
    statuses: dict[str, Availability | None] = {}
    sources: dict[str, str] = {}
    pool: list[str] = []
    if availability is AvailabilityMode.REALIZED:
        # Grading a finished season: a week the player missed is a week the manager lost the
        # category, so measuring weekly totals directly is correct and must not change.
        stats, pool = measure_period_stats(
            store, season, categories, pool_size, include_idle_weeks=True
        )
    else:
        # Forward boards are *constructed*, never measured at the week level: see
        # `compound_weekly`. Aggregating to weeks first would smuggle realized availability
        # back in through each player's games-per-active-week.
        per_game, pool = measure_per_game_stats(
            store, season, categories, pool_size, forward_season=forward_season,
            min_games=BASELINE_MIN_GAMES, sources=sources,
        )
        if rate_basis == "projected":
            # R4/D3: hand the per-game rates to the derived minutes/role model, which
            # projects usage onto the player's forward-season roster. The pool stays
            # measured — who is ranked does not move — and the standardisation bases below
            # are recomputed over the stats actually ranked, so every row is compared
            # inside the basis it was priced in.
            if not forward_season:
                raise ValueError("projected rates need a forward season to project onto")
            if not as_of:
                raise ValueError("projected rates need an as-of date (the projection date)")
            for pid, (tag, per_cat) in _projected_per_game(
                store, pool, categories, season, forward_season, as_of,
            ).items():
                per_game[pid] = per_cat
                sources[pid] = tag
        n_sched = scheduled_games_per_week(store, season)
        if availability is AvailabilityMode.PROJECTED:
            if not as_of:
                raise ValueError("projected availability needs an --as-of date")
            statuses = status_for_pool(store, pool, as_of)
            projections = project_availability(store, season, as_of, players=pool,
                                               statuses=statuses)
            rates = {p: getattr(g, "availability_rate", 1.0) for p, g in projections.items()}
        else:
            rates = dict.fromkeys(per_game, 1.0)
        stats = compound_weekly(per_game, rates, n_sched)

    scored_players = [p for p in pool if p in stats]
    bases: dict[str, CategoryBasis] = {}
    for c in categories:
        means = [stats[p][c].mean for p in scored_players]
        taus = [stats[p][c].std for p in scored_players]
        bases[c] = CategoryBasis(
            category=c,
            pool_mean=fmean(means) if means else 0.0,
            pool_std=(pstdev(means) if len(means) > 1 else 0.0) or 1e-9,
            typical_tau=median(taus) if taus else 0.0,
        )
    basis = XScoreBasis(
        categories=categories, bases=bases, stats=stats, pool=scored_players,
        kappa=kappa, mode=mode,
    )
    return basis, projections, statuses, sources, pool


def build_board(
    store,
    season: str,
    punt: tuple[str, ...] | list[str] = (),
    build: str | None = None,
    pool_size: int = 156,
    kappa: float = BOARD_KAPPA,
    mode: VarianceMode = VarianceMode.MEASURED,
    availability: AvailabilityMode = AvailabilityMode.PROJECTED,
    as_of: str | None = None,
    limit: int | None = None,
    with_zscore: bool = True,
    forward_season: str | None = None,
    rate_basis: str = "measured",
) -> Board:
    """Rank the pool by G-score over the categories left after ``punt``.

    ``punt`` drops categories from the scored set entirely — it does not reweight them — which
    is what a punt build means and what makes the result comparable to the punt checkbox in
    every commercial tool. The z-score comparison, when requested, is computed over the *same*
    reduced category set and the same pool, so the delta isolates the metric and nothing else.

    ``forward_season`` names the season being drafted into (e.g. 2026-27): players the ranked
    season cannot place are ranked by derived depth on that season's projected roster, priced
    per-game from their last healthy season, and the provenance line records both sources.

    Platform status is consumed in the ``projected`` availability mode: effective-dated
    designations (task 2.1) cap the availability rate, rows carry the status and its dated
    note, and every player nothing could price is reported in ``Board.unpriced`` rather than
    silently dropped.

    ``rate_basis`` picks the per-game rate source (R4/D3): ``measured`` — the ranked season,
    with the task-2.2 last-healthy baseline behind it; ``projected`` — the derived
    minutes/role model carries current usage onto the forward roster (the rates are measured
    on ``as_of`` and projected onto the player's 2026-27 team). The pool is unchanged either
    way; under ``projected`` the standardisation is recomputed over the projected stats, so
    the rank stays internally coherent in the basis it describes.
    """
    punt = tuple(punt)
    unknown = [c for c in punt if c not in DEFAULT_CATEGORIES]
    if unknown:
        raise ValueError(f"not 9-cat categories: {unknown} (known: {DEFAULT_CATEGORIES})")
    scored = [c for c in DEFAULT_CATEGORIES if c not in punt]
    if not scored:
        raise ValueError("cannot punt every category")
    if rate_basis not in RATE_BASES:
        raise ValueError(f"unknown rate basis {rate_basis!r} (known: {RATE_BASES})")
    if rate_basis == "projected" and availability is AvailabilityMode.REALIZED:
        raise ValueError("projected rates rank a season not yet played; `realized` "
                         "grades a finished season — the two cannot combine")
    rate_basis = RateBasis(rate_basis)

    basis, projections, statuses, sources, pool = _basis(
        store, season, scored, pool_size, kappa, mode, availability, as_of,
        forward_season=forward_season, rate_basis=rate_basis,
    )
    ranked = sorted(
        (
            (p, round(basis.total(p), 4),
             {c: round(basis.category_score(p, c), 4) for c in basis.categories})
            for p in basis.pool
        ),
        key=lambda r: -r[1],
    )

    z_rank: dict[str, int] = {}
    if with_zscore:
        # Same pool, same categories, season-long z-score: the market's metric, so the delta
        # measures the metric change alone rather than a difference in who was considered.
        zvals = player_values(store, season, pool_size=pool_size, categories=scored)
        ordered = sorted(zvals.items(), key=lambda kv: (-kv[1], kv[0]))
        z_rank = {pid: i for i, (pid, _) in enumerate(ordered, start=1)}

    # Players the pool holds but nothing could price are reported, never silently dropped
    # (R3): no ranked-season sample, and no earlier season cleared the baseline floor.
    unpriced_ids = [pid for pid in pool if pid not in basis.stats]
    ids = [pid for pid, _, _ in ranked]
    names = _player_names(store, season, ids + unpriced_ids)
    unpriced = tuple(
        (pid, names.get(pid, pid),
         f"no usable {season} sample and no earlier season above {BASELINE_MIN_GAMES} games")
        for pid in unpriced_ids
    )

    rows = [
        BoardRow(
            rank=i,
            player_id=pid,
            player_name=names.get(pid, pid),
            total=total,
            categories=cats,
            expected_games=(
                round(getattr(projections[pid], "expected_games", 0.0), 1)
                if pid in projections else None
            ),
            availability_rate=(
                round(getattr(projections[pid], "availability_rate", 0.0), 3)
                if pid in projections else None
            ),
            z_rank=z_rank.get(pid),
            z_delta=(z_rank[pid] - i) if pid in z_rank else None,
            status=(s.status if (s := statuses.get(pid)) else None),
            status_note=(s.note or None if (s := statuses.get(pid)) else None),
            rate_source=sources.get(pid),
        )
        for i, (pid, total, cats) in enumerate(ranked, start=1)
    ]
    status_counts: dict[str, int] = {}
    for s in statuses.values():
        if s is not None and s.status != ACTIVE:
            status_counts[s.status] = status_counts.get(s.status, 0) + 1
    return Board(
        season=season,
        build=build or _build_name(punt),
        punt=punt,
        categories=tuple(scored),
        pool_size=pool_size,
        kappa=kappa,
        variance_mode=str(mode),
        availability=availability,
        availability_as_of=as_of,
        forward_season=forward_season,
        rate_basis=rate_basis,
        projected_as_of=as_of if rate_basis == "projected" else None,
        status_as_of=as_of if statuses else None,
        status_counts=status_counts,
        rate_sources=dict(sources),
        unpriced=unpriced,
        rows=rows[:limit] if limit else rows,
    )


def _build_name(punt: tuple[str, ...]) -> str:
    for name, cats in PUNT_BUILDS.items():
        if cats == punt:
            return name
    return "punt_" + "_".join(punt) if punt else "balanced"


def all_builds(store, season: str, builds: list[str] | None = None, **kwargs) -> list[Board]:
    """Every named build for one season, for a single export pass."""
    wanted = builds or list(PUNT_BUILDS)
    unknown = [b for b in wanted if b not in PUNT_BUILDS]
    if unknown:
        raise ValueError(f"unknown builds: {unknown} (known: {list(PUNT_BUILDS)})")
    return [build_board(store, season, PUNT_BUILDS[b], build=b, **kwargs) for b in wanted]


def biggest_movers(board: Board, n: int = 10) -> tuple[list[BoardRow], list[BoardRow]]:
    """``(underrated, overrated)`` by z-score delta — the players the two metrics disagree on.

    Restricted to rows that hold a z-rank; a player the z-score pool ranked but the G-score
    pool did not (or vice versa) has no meaningful delta and is left out rather than sorted
    against a null.
    """
    rated = [r for r in board.rows if r.z_delta is not None]
    by_delta = sorted(rated, key=lambda r: (-(r.z_delta or 0), r.rank))
    return by_delta[:n], list(reversed(by_delta[-n:]))


def board_json(board: Board, top: int | None = None) -> dict:
    """Serialisable form. ``basis`` is included deliberately — see the module docstring."""
    rows = board.rows[:top] if top else board.rows
    return {
        "season": board.season,
        "build": board.build,
        "label": board.label,
        "punt": list(board.punt),
        "categories": list(board.categories),
        "pool_size": board.pool_size,
        "kappa": board.kappa,
        "variance_mode": board.variance_mode,
        "availability": str(board.availability),
        "availability_as_of": board.availability_as_of,
        "forward_season": board.forward_season,
        "rate_basis": str(board.rate_basis),
        "projected_as_of": board.projected_as_of,
        "status_as_of": board.status_as_of,
        "unpriced": [list(u) for u in board.unpriced],
        "basis": board.basis,
        "rows": [
            {
                "rank": r.rank,
                "player_id": r.player_id,
                "player_name": r.player_name,
                "g_score": r.total,
                "expected_games": r.expected_games,
                "availability_rate": r.availability_rate,
                "z_rank": r.z_rank,
                "z_delta": r.z_delta,
                "status": r.status,
                "status_note": r.status_note,
                "rate_source": r.rate_source,
                "categories": r.categories,
            }
            for r in rows
        ],
    }


def render_table(board: Board, top: int = 30) -> str:
    """Plain-text board for the terminal."""
    lines = [
        f"{board.label} board — {board.season}  "
        f"({len(board.categories)} cats, pool {board.pool_size}, κ={board.kappa})",
        board.basis,
        "",
        f"{'#':>3}  {'player':<26} {'G':>7}  {'vs z':>6}  {'gp':>5}  {'st':>3}  top categories",
    ]
    shown = board.rows[:top]
    for r in shown:
        delta = "—" if r.z_delta is None else f"{r.z_delta:+d}"
        gp = "—" if r.expected_games is None else f"{r.expected_games:.0f}"
        st = {"OUT": "OUT", "QUESTIONABLE": "QUE", "ACTIVE": "act"}.get(r.status or "", "")
        name = r.player_name + ("*" if r.rate_source else "")
        best = sorted(r.categories.items(), key=lambda kv: -kv[1])[:3]
        cats = " ".join(f"{c}{v:+.2f}" for c, v in best)
        lines.append(
            f"{r.rank:>3}  {name:<26} {r.total:>+7.3f}  {delta:>6}  {gp:>5}  {st:>3}  {cats}"
        )
        context = []
        if r.status_note:
            context.append(f"{r.status} — {r.status_note}")
        if r.rate_source:
            context.append(f"rates priced from {r.rate_source.split(':', 1)[1]}")
        if context:
            lines.append(" " * 61 + "· " + " — ".join(context))
    if any(r.rate_source for r in shown):
        lines.append("* rates priced from an earlier healthy season")
    if board.unpriced:
        lines.append(f"unpriced ({len(board.unpriced)}):")
        for _pid, name, reason in board.unpriced[:10]:
            lines.append(f"  {name}: {reason}")
    return "\n".join(lines)


def render_markdown(board: Board, top: int = 150) -> str:
    """Publishable Markdown — the form the eventual page and any thread renders from."""
    lines = [
        f"# {board.label} — {board.season} 9-cat draft board",
        "",
        board.basis,
        "",
        f"Ranked by **G-score** over {len(board.categories)} categories "
        f"(`{'`, `'.join(board.categories)}`), κ={board.kappa}, "
        f"per-player variance `{board.variance_mode}`.",
        "",
        "`vs z` is the player's rank under season-long z-score minus their rank here. "
        "**Positive means z-score underrates them.** `exp GP` is projected games played — "
        "shown as its own column precisely so the availability effect can be read off "
        "separately from the variance effect rather than being conflated with it.",
        "",
        "| # | Player | G-score | vs z | exp GP |"
        + "".join(f" {c} |" for c in board.categories)
        + " st | note |",
        "|--:|---|--:|--:|--:|" + "--:|" * len(board.categories) + "---|---|",
    ]
    for r in board.rows[:top]:
        delta = "—" if r.z_delta is None else f"{r.z_delta:+d}"
        gp = "—" if r.expected_games is None else f"{r.expected_games:.0f}"
        st = {"OUT": "OUT", "QUESTIONABLE": "QUE"}.get(r.status or "", "")
        note = (r.status_note or "").replace("|", "/")
        cells = "".join(f" {r.categories.get(c, 0.0):+.2f} |" for c in board.categories)
        star = "*" if r.rate_source else ""
        lines.append(
            f"| {r.rank} | {r.player_name}{star} | {r.total:+.3f} | {delta} | {gp} |{cells}"
            f" {st} | {note} |"
        )
    if any(r.rate_source for r in board.rows[:top]):
        lines.append("")
        lines.append("\\* rates priced from an earlier healthy season "
                     "(see `rate_source` in the JSON export)")
    return "\n".join(lines) + "\n"


def export(boards: list[Board], out_dir, top: int | None = None) -> list[str]:
    """Write ``<build>.json`` and ``<build>.md`` per board, plus an ``index.json`` manifest.

    The manifest is what a page reads to discover the builds without hard-coding the list, so
    adding a build to :data:`PUNT_BUILDS` is enough to publish it.
    """
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for b in boards:
        payload = board_json(b, top=top)
        (out / f"{b.build}.json").write_text(json.dumps(payload, indent=2) + "\n")
        (out / f"{b.build}.md").write_text(render_markdown(b, top=top or 150))
        written += [str(out / f"{b.build}.json"), str(out / f"{b.build}.md")]
    manifest = {
        "season": boards[0].season if boards else None,
        "basis": boards[0].basis if boards else None,
        "builds": [
            {"build": b.build, "label": b.label, "punt": list(b.punt), "players": len(b.rows)}
            for b in boards
        ],
    }
    (out / "index.json").write_text(json.dumps(manifest, indent=2) + "\n")
    written.append(str(out / "index.json"))
    return written
