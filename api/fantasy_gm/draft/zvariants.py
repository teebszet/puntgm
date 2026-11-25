"""Industry z-score, steelmanned — the baseline G-score actually has to beat.

:mod:`fantasy_gm.valuation` computes the z-score this project has been quoting as the
incumbent: per-game means standardised over a rosterable pool, percentage categories in
volume-weighted impact form. That is a competent z-score and it is what every free ranking
list publishes. **It is not what a drafter using a serious tool holds in their hand**, and
beating it is therefore a weaker result than it sounds.

The gap that matters is *availability*. Basketball Monster and Hashtag Basketball both expose
a per-game / total toggle, and the total form multiplies value by games played. A board built
that way is not blind to who plays. Since the published finding "z-score cannot see
availability" rests on the per-game form, the honest baseline has to include the total form
too, or the comparison is a strawman — which this project's standing rule forbids.

This module parameterises the three tweaks that plausibly matter in 9-cat, so each can be
switched on in replay and *measured* rather than argued about:

1. :class:`Availability` — per-game (``NONE``), × games actually played (``REALIZED``, a
   hindsight ceiling), or × expected games projected from history strictly before a cut date
   (``PROJECTED``). ``PROJECTED`` reads the same A13 beta-binomial model the G-score board
   uses, so a steelman z arm and the G arm receive *identical* availability information and
   whatever separates them is the weekly-variance term alone. That is the number worth
   publishing.
2. ``replacement_iters`` — standardise over the players who would actually be *rostered*
   rather than the top ``pool_size`` by minutes, found by iterating value → pool → value.
   Needs a candidate universe wider than the pool or the iteration is a no-op.
3. ``categories`` — a punt build's reduced category set, so a punt-aware z arm can be matched
   build-for-build against a punt-aware G board.

**Deliberately duplicated arithmetic.** The standardisation below re-implements what
``valuation.player_values`` does rather than importing it, because that function memoises on a
fixed pool and exposes no hook for a scale factor or an iterated pool. The guard against the
two drifting apart is a test asserting they agree *exactly* under the default settings
(``Availability.NONE``, no iteration) — if this file ever stops reproducing the shipped
z-score, that test fails rather than the replay quietly comparing against something else.
"""

from __future__ import annotations

from enum import StrEnum
from statistics import fmean, pstdev

from fantasy_gm.config import CATEGORY_DIRECTION, DEFAULT_CATEGORIES, PERCENTAGE_CATEGORIES
from fantasy_gm.valuation import _player_games, rosterable_pool

# The candidate universe is wider than the standardisation pool on purpose: replacement-level
# iteration works by letting players outside the current pool displace players inside it, and
# if the universe equals the pool there is nobody to promote and the iteration is a no-op that
# looks like convergence.
DEFAULT_UNIVERSE = 300


class Availability(StrEnum):
    """How a z-score arm accounts for games played."""

    NONE = "none"            # per-game value — the form every free ranking list publishes
    REALIZED = "realized"    # × games actually played; hindsight, so a ceiling not a baseline
    PROJECTED = "projected"  # × expected games from the fitted A13 model, cut at ``as_of``
    NAIVE = "naive"          # × last season's games played — what a drafter does by eye


def games_scale(
    store,
    season: str,
    pool: list[str],
    games: dict[str, list[dict]],
    availability: Availability,
    as_of: str | None = None,
) -> dict[str, float]:
    """``{player_id: multiplier}`` turning per-game means into season totals.

    ``REALIZED`` uses the games the player actually played, which is hindsight — it is here to
    size the prize, not to be quoted as a baseline. ``PROJECTED`` reuses
    :func:`fantasy_gm.draft.board.project_availability`, so a rookie with no prior appearances
    takes the fitted pool rate rather than defaulting to a full season.
    """
    if availability is Availability.NONE:
        return dict.fromkeys(pool, 1.0)
    if availability is Availability.REALIZED:
        return {p: float(len(games.get(p, ()))) for p in pool}
    if not as_of:
        raise ValueError("projected availability needs an as_of date")
    if availability is Availability.NAIVE:
        # The comparison that says what the A13 model is actually worth: last season's games
        # played, carried forward unshrunk, which is what a drafter reads off a stat page.
        # Anyone with no prior season takes the pool mean rather than a full season, for the
        # same reason the fitted model does — defaulting to 82 would rank every rookie as an
        # ironman.
        prior = {
            r["player_id"]: r["n"]
            for r in store.conn.execute(
                "SELECT player_id, COUNT(*) n FROM player_logs WHERE game_date < ? "
                "AND game_date >= date(?, '-1 year') GROUP BY player_id",
                (as_of, as_of),
            )
        }
        seen = [prior[p] for p in pool if p in prior]
        fallback = float(sum(seen)) / len(seen) if seen else 65.0
        return {p: float(prior.get(p, fallback)) for p in pool}
    from fantasy_gm.draft.board import project_availability

    projections = project_availability(store, season, as_of, players=list(pool))
    return {
        p: max(float(getattr(projections.get(p), "expected_games", 0.0) or 0.0), 0.0)
        for p in pool
    }


def _standardise(
    games: dict[str, list[dict]],
    pool: list[str],
    universe: list[str],
    categories: list[str],
    scale: dict[str, float],
) -> dict[str, float]:
    """Score every player in ``universe`` against baselines measured over ``pool``.

    Splitting the two is what makes replacement-level iteration expressible: the pool defines
    what "average" means, the universe defines who is being ranked.
    """
    counting = [c for c in categories if c not in PERCENTAGE_CATEGORIES]
    pcts = [c for c in categories if c in PERCENTAGE_CATEGORIES]

    agg: dict[str, dict[str, float]] = {}
    for pid in universe:
        gs = games[pid]
        k = scale.get(pid, 1.0)
        rec: dict[str, float] = {c: fmean([g.get(c, 0.0) for g in gs]) * k for c in counting}
        for c in pcts:
            mk, at = PERCENTAGE_CATEGORIES[c]
            made = sum(g.get(mk, 0.0) for g in gs)
            att = sum(g.get(at, 0.0) for g in gs)
            # The rate is a rate whatever the scale; only the volume it is applied over moves.
            rec[f"{c}_pct"] = made / att if att > 0 else 0.0
            rec[f"{c}_att"] = fmean([g.get(at, 0.0) for g in gs]) * k
        agg[pid] = rec

    base: dict[str, tuple[float, float]] = {}
    for c in counting:
        vals = [agg[p][c] for p in pool]
        base[c] = (fmean(vals), pstdev(vals) or 1.0)

    impact_base: dict[str, tuple[float, float, float]] = {}
    for c in pcts:
        mk, at = PERCENTAGE_CATEGORIES[c]
        # The league rate is measured from raw season totals over the pool and is deliberately
        # *not* rescaled by the availability arm: a season's pooled FG% is a fact about the
        # league, not a function of how a board chooses to value games played. Scaling it too
        # would move the replacement line under every arm and confound the comparison.
        tot_made = sum(g.get(mk, 0.0) for p in pool for g in games[p])
        tot_att = sum(g.get(at, 0.0) for p in pool for g in games[p])
        league_pct = tot_made / tot_att if tot_att > 0 else 0.0
        impacts = [(agg[p][f"{c}_pct"] - league_pct) * agg[p][f"{c}_att"] for p in pool]
        impact_base[c] = (league_pct, fmean(impacts), pstdev(impacts) or 1.0)

    values: dict[str, float] = {}
    for pid in universe:
        z = 0.0
        for c in counting:
            mean, std = base[c]
            z += CATEGORY_DIRECTION[c] * (agg[pid][c] - mean) / std
        for c in pcts:
            league_pct, mean_imp, std_imp = impact_base[c]
            imp = (agg[pid][f"{c}_pct"] - league_pct) * agg[pid][f"{c}_att"]
            z += (imp - mean_imp) / std_imp
        values[pid] = round(z, 4)
    return values


def z_values(
    store,
    season: str,
    categories: list[str] | None = None,
    pool_size: int = 156,
    availability: Availability = Availability.NONE,
    as_of: str | None = None,
    replacement_iters: int = 0,
    universe_size: int = DEFAULT_UNIVERSE,
) -> dict[str, float]:
    """``{player_id: z-value}`` under one combination of the industry tweaks.

    Defaults reproduce :func:`fantasy_gm.valuation.player_values` exactly — that equality is
    asserted in the tests, and is what lets a steelman arm be read as "the shipped baseline
    plus these named tweaks" rather than as a different metric.
    """
    categories = list(categories or DEFAULT_CATEGORIES)
    games = _player_games(store, season)
    if not games:
        return {}
    pool = rosterable_pool(store, season, pool_size=pool_size, games=games)
    universe = (
        rosterable_pool(store, season, pool_size=max(universe_size, pool_size), games=games)
        if replacement_iters
        else pool
    )
    scale = games_scale(store, season, universe, games, availability, as_of)

    values = _standardise(games, pool, universe, categories, scale)
    for _ in range(replacement_iters):
        promoted = sorted(universe, key=lambda p: (-values[p], p))[:pool_size]
        if promoted == pool:
            break
        pool = promoted
        values = _standardise(games, pool, universe, categories, scale)
    return values


def z_order(store, season: str, **kwargs) -> list[str]:
    """The draft board a z-score arm reads down, best first."""
    values = z_values(store, season, **kwargs)
    return sorted(values, key=lambda p: (-values[p], p))


# The ladder run in replay. Each entry is a kwargs bundle for :func:`z_values`, so an arm is
# defined by exactly which tweaks it turns on and nothing else.
#
# ``steelman`` is the arm the published claim must beat: it gets the same forward availability
# information the G-score board gets, plus replacement-level standardisation. ``total_realized``
# is deliberately *not* a baseline — it knows who got hurt, and is included only to bound how
# much of the gap availability can possibly explain.
Z_ARMS: dict[str, dict] = {
    "z_pergame": {},
    "z_total_realized": {"availability": Availability.REALIZED},
    "z_total_projected": {"availability": Availability.PROJECTED},
    "z_replacement": {"replacement_iters": 5},
    "z_steelman": {"availability": Availability.PROJECTED, "replacement_iters": 5},
    "z_total_naive": {"availability": Availability.NAIVE},
}

# Arms that use hindsight and may not be quoted as a baseline in any published comparison.
HINDSIGHT_ARMS = frozenset({"z_total_realized"})
