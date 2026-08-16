"""Percentage categories are ranked by volume-weighted impact, never by rate."""

from __future__ import annotations

from fantasy_gm.config import Config
from fantasy_gm.data.store import Store
from fantasy_gm.engine.reconcile import Reconciler
from fantasy_gm.models import Game, PlayerGameLog
from fantasy_gm.valuation import clear_value_cache, league_percentage_rates

SEASON = "2025-26"
DATES = [f"2025-11-{d:02d}" for d in range(1, 11)]


def _store() -> Store:
    """A league where the pool shoots ~50% on volume, plus two wire options:

    * ``sniper``  — 3-for-3 every night (100%), negligible volume
    * ``anchor``  — 8-for-12 every night (66.7%), real volume
    """
    store = Store(":memory:")
    store.upsert_games([
        Game(f"g{i}", SEASON, d, "LAL", "BOS", 100, 99) for i, d in enumerate(DATES)
    ])
    logs = []
    for i, d in enumerate(DATES):
        # 20 pool players shooting 50% on 10 attempts set the league rate
        for p in range(20):
            logs.append(PlayerGameLog(f"g{i}", SEASON, d, f"pool{p}", f"Pool {p}", "LAL",
                                      {"fgm": 5.0, "fga": 10.0, "pts": 10.0}))
        logs.append(PlayerGameLog(f"g{i}", SEASON, d, "sniper", "The Sniper", "LAL",
                                  {"fgm": 3.0, "fga": 3.0, "pts": 6.0}))
        logs.append(PlayerGameLog(f"g{i}", SEASON, d, "anchor", "The Anchor", "LAL",
                                  {"fgm": 8.0, "fga": 12.0, "pts": 16.0}))
    store.upsert_player_logs(logs)
    clear_value_cache()
    return store


def test_league_rate_reflects_pool_volume():
    assert abs(league_percentage_rates(_store(), SEASON)["fg_pct"] - 0.5) < 0.02


def test_volume_shooter_outranks_a_perfect_low_volume_one():
    """A 100% shooter on 3 attempts barely moves a roster's aggregate FG%; a 66.7% shooter
    on 12 attempts moves it a lot. Ranking on rate alone gets this exactly backwards."""
    store, rec = _store(), Reconciler(Config())
    sniper = rec._cat_recent(store, "sniper", DATES[-1], "fg_pct", SEASON)
    anchor = rec._cat_recent(store, "anchor", DATES[-1], "fg_pct", SEASON)
    assert anchor > sniper


def test_below_league_rate_shooter_scores_negative():
    """Impact is signed: a poor shooter on volume actively hurts the category. The rate
    form could only ever say 'less good'."""
    store = _store()
    store.upsert_player_logs([
        PlayerGameLog(f"g{i}", SEASON, d, "brick", "The Brick", "LAL",
                      {"fgm": 3.0, "fga": 12.0, "pts": 6.0})
        for i, d in enumerate(DATES)
    ])
    clear_value_cache()
    assert Reconciler(Config())._cat_recent(store, "brick", DATES[-1], "fg_pct", SEASON) < 0


def test_impact_is_scaled_by_participation():
    """Volume is what missed games dilute — the shooting rate itself is unaffected."""
    store = _store()
    # a half-time version of the anchor: identical line, but only appears every other game
    store.upsert_player_logs([
        PlayerGameLog(f"g{i}", SEASON, d, "half", "Half Timer", "LAL",
                      {"fgm": 8.0, "fga": 12.0, "pts": 16.0})
        for i, d in enumerate(DATES) if i % 2 == 0
    ])
    clear_value_cache()
    rec = Reconciler(Config())
    full = rec._cat_recent(store, "anchor", DATES[-1], "fg_pct", SEASON)
    half = rec._cat_recent(store, "half", DATES[-1], "fg_pct", SEASON)
    assert 0 < half < full


def test_non_actionable_categories_are_never_targeted():
    """A15: ft_pct is contested often but unmovable by a waiver add, so the engine must not
    spend its one move on it. The gain filter (`> 0.01` win prob) does not catch this —
    projected FT% gains are large (median 18pp); they just don't materialise."""
    cfg = Config()
    assert "ft_pct" in cfg.non_actionable_categories
    assert "fg_pct" not in cfg.non_actionable_categories


def test_actionability_is_configurable_not_hardcoded():
    """A different league (or a later measurement) can change the set without a code edit."""
    from dataclasses import replace

    assert replace(Config(), non_actionable_categories=frozenset()).non_actionable_categories \
        == frozenset()
