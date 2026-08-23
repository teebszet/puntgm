"""The steelmanned z-score baseline.

The load-bearing test here is :func:`test_defaults_reproduce_the_shipped_z_score`. This module
re-implements the standardisation in :mod:`fantasy_gm.valuation` rather than importing it, so
the only thing stopping the two from silently diverging — and the replay from comparing
G-score against a baseline nobody ships — is that equality holding exactly.

The rest pin the individual tweaks apart, so a later change cannot collapse "total value" back
into "per-game value" without a test noticing.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fantasy_gm.data.store import Store
from fantasy_gm.draft.zvariants import (
    Z_ARMS,
    Availability,
    games_scale,
    z_order,
    z_values,
)
from fantasy_gm.models import Game, PlayerGameLog, UsageRole
from fantasy_gm.valuation import clear_value_cache, player_values

SEASON = "2025-26"
START = date(2025, 10, 20)
AS_OF = "2025-10-19"  # before the season: nothing in SEASON is visible to a projection


def _line(**c):
    base = {k: 0.0 for k in ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov",
                             "fgm", "fga", "ftm", "fta")}
    base.update(c)
    return base


def _seed(store: Store, players: dict[str, list[dict | None]], minutes: float = 30.0):
    n_days = max(len(v) for v in players.values())
    for day_i in range(n_days):
        d = (START + timedelta(days=day_i)).isoformat()
        store.upsert_games([Game(f"g{day_i}", SEASON, d, "AAA", "BBB")])
        for pid, lines in players.items():
            if day_i < len(lines) and lines[day_i] is not None:
                store.upsert_player_logs(
                    [PlayerGameLog(f"g{day_i}", SEASON, d, pid, pid, "AAA", lines[day_i])]
                )
                store.add_usage_role([UsageRole(pid, d, minutes, 12.0, True, 1)])


def _store() -> Store:
    """A durable star, an identical star who misses half the season, and filler."""
    store = Store(":memory:")
    _seed(store, {
        "durable": [_line(pts=25, reb=6, ast=5, fgm=9, fga=18) for _ in range(40)],
        "fragile": [_line(pts=25, reb=6, ast=5, fgm=9, fga=18)
                    if i % 2 == 0 else None for i in range(40)],
        "grinder": [_line(pts=12, reb=8, ast=2, fgm=5, fga=11) for _ in range(40)],
        "filler":  [_line(pts=6, reb=3, ast=1, fgm=2, fga=6) for _ in range(40)],
    })
    clear_value_cache()
    return store


# --- the anti-drift guard ----------------------------------------------------


def test_defaults_reproduce_the_shipped_z_score():
    """Default settings must equal ``valuation.player_values`` to the last decimal.

    If this fails, the replay's z-score arm is no longer the z-score the rest of the project
    computes, and every comparison built on it is measuring something unnamed.
    """
    store = _store()
    shipped = player_values(store, SEASON, pool_size=4)
    variant = z_values(store, SEASON, pool_size=4)
    assert set(shipped) == set(variant)
    assert all(shipped[p] == variant[p] for p in shipped)


# --- availability ------------------------------------------------------------


def test_per_game_value_cannot_see_who_played():
    """Two players with identical per-game lines tie, however many games they played."""
    store = _store()
    v = z_values(store, SEASON, pool_size=4)
    assert v["durable"] == v["fragile"]


def test_total_value_separates_them():
    """The tweak that matters: multiplying by games breaks the tie the per-game form cannot."""
    store = _store()
    v = z_values(store, SEASON, pool_size=4, availability=Availability.REALIZED)
    assert v["durable"] > v["fragile"]


def test_realized_scale_is_games_played():
    store = _store()
    games = {"durable": 40, "fragile": 20, "grinder": 40, "filler": 40}
    scale = games_scale(store, SEASON, list(games), {
        p: [None] * n for p, n in games.items()
    }, Availability.REALIZED)
    assert scale == {p: float(n) for p, n in games.items()}


def test_projected_availability_needs_a_cut_date():
    """A forward arm without an as_of would silently fall back to hindsight."""
    store = _store()
    with pytest.raises(ValueError, match="as_of"):
        z_values(store, SEASON, pool_size=4, availability=Availability.PROJECTED)


def test_projection_does_not_read_the_season_it_ranks():
    """Cut before the season starts and no player has history, so nobody is separated by it."""
    store = _store()
    v = z_values(store, SEASON, pool_size=4, availability=Availability.PROJECTED,
                 as_of=AS_OF)
    assert v["durable"] == pytest.approx(v["fragile"])


# --- replacement level -------------------------------------------------------


def test_replacement_iteration_needs_a_wider_universe_than_the_pool():
    """With universe == pool there is nobody to promote, so the iteration must be inert."""
    store = _store()
    flat = z_values(store, SEASON, pool_size=4)
    iterated = z_values(store, SEASON, pool_size=4, replacement_iters=5, universe_size=4)
    assert flat == iterated


def test_replacement_iteration_restandardises_on_the_promoted_pool():
    store = _store()
    wide = z_values(store, SEASON, pool_size=2, replacement_iters=5, universe_size=4)
    assert len(wide) == 4                       # everyone in the universe is scored
    top = sorted(wide, key=lambda p: -wide[p])[:2]
    assert set(top) == {"durable", "fragile"}   # the pool converged onto the best two


# --- punt awareness ----------------------------------------------------------


def test_a_punt_removes_the_category_from_the_z_baseline_too():
    """A punt-aware z arm is the fair opponent for a punt-aware G board."""
    store = _store()
    full = z_values(store, SEASON, pool_size=4)
    punted = z_values(store, SEASON, pool_size=4,
                      categories=[c for c in ("pts", "reb", "ast") if c != "reb"])
    assert full != punted


# --- the ladder --------------------------------------------------------------


def test_every_arm_produces_a_complete_ordering():
    store = _store()
    for arm, kwargs in Z_ARMS.items():
        order = z_order(store, SEASON, pool_size=4, as_of=AS_OF, **kwargs)
        assert len(order) == len(set(order)), arm
        assert {"durable", "fragile", "grinder", "filler"} <= set(order), arm
