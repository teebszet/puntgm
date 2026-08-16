"""The variance-aware basis and its static G-score reduction (Phase 1)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fantasy_gm.data.store import Store
from fantasy_gm.draft.xscore import (
    VarianceMode,
    g_score_board,
    g_scores,
    kappa_sensitivity,
    measure_period_stats,
    xscore_basis,
)
from fantasy_gm.models import Game, PlayerGameLog, UsageRole
from fantasy_gm.valuation import player_values

SEASON = "2025-26"
START = date(2025, 10, 20)  # a Monday


def _line(**c):
    base = {k: 0.0 for k in ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov",
                             "fgm", "fga", "ftm", "fta")}
    base.update(c)
    return base


def _seed(store: Store, players: dict[str, list[dict | None]], minutes: float = 30.0):
    """``players[pid]`` is one box line per game-day; None means the player sat that day."""
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


# --- the core claim ----------------------------------------------------------


def _steady_vs_swingy() -> Store:
    """Two players with identical weekly means and very different weekly spread, plus a
    replacement-level third so the pool mean sits below them and the numerator is non-zero.

    (With only the two, both numerators are exactly zero and no denominator can separate
    them — the standardisation is relative to the pool, so the pool has to have a shape.)
    """
    store = Store(":memory:")
    _seed(store, {
        "steady": [_line(pts=20) for _ in range(28)],
        "swingy": [_line(pts=40 if i % 2 else 0) for i in range(28)],
        "replacement": [_line(pts=8) for _ in range(28)],
    })
    return store


def test_equal_means_different_variance_are_valued_differently():
    """The whole point: two players averaging the same points differ in H2H value when one
    is steady and the other is boom-or-bust. Z-score cannot see this."""
    store = _steady_vs_swingy()
    vals = g_scores(store, SEASON, categories=["pts"], pool_size=3)
    assert vals["steady"] > vals["swingy"]

    # ...and the existing z-score baseline is blind to it (same per-game mean).
    z = player_values(store, SEASON, pool_size=3, categories=["pts"])
    assert z["steady"] == pytest.approx(z["swingy"])


def test_kappa_zero_removes_the_variance_term():
    """κ=0 reduces to a period-aggregated z-score, so the two players tie again — which is
    what makes κ the knob that turns the correction on."""
    vals = g_scores(_steady_vs_swingy(), SEASON, categories=["pts"], pool_size=3, kappa=0.0)
    assert vals["steady"] == pytest.approx(vals["swingy"], abs=1e-6)


def test_uniform_mode_hides_the_difference_measured_mode_sees():
    """A-DRAFT-1: the paper assumes one τ for everyone. Under that assumption the steady and
    swingy players are indistinguishable; with measured τ they are not."""
    store = _steady_vs_swingy()
    uniform = g_scores(store, SEASON, ["pts"], 3, mode=VarianceMode.UNIFORM)
    measured = g_scores(store, SEASON, ["pts"], 3, mode=VarianceMode.MEASURED)
    assert uniform["steady"] == pytest.approx(uniform["swingy"], abs=1e-6)
    assert measured["steady"] > measured["swingy"]


# --- periods and idle weeks --------------------------------------------------


def test_idle_weeks_count_as_zero_and_raise_variance():
    """A rostered player who does not take the floor scores nothing. Dropping those weeks
    would understate τ for exactly the fragile players a draft must price (participation
    finding: 31% of adds never played)."""
    full = [_line(pts=20) for _ in range(28)]
    # plays weeks 1 and 3, sits weeks 2 and 4 — same per-game average when he plays
    intermittent = [
        _line(pts=20) if (i // 7) % 2 == 0 else None for i in range(28)
    ]
    store = Store(":memory:")
    _seed(store, {"full": full, "intermittent": intermittent})

    with_idle, _ = measure_period_stats(store, SEASON, ["pts"], pool_size=2)
    without_idle, _ = measure_period_stats(
        store, SEASON, ["pts"], pool_size=2, include_idle_weeks=False
    )
    assert with_idle["intermittent"]["pts"].std > without_idle["intermittent"]["pts"].std
    assert with_idle["intermittent"]["pts"].mean < with_idle["full"]["pts"].mean


def test_measured_stats_count_weeks_not_games():
    store = Store(":memory:")
    _seed(store, {"p": [_line(pts=10) for _ in range(14)]})
    stats, _ = measure_period_stats(store, SEASON, ["pts"], pool_size=1)
    assert stats["p"]["pts"].periods == 2          # 14 days = 2 ISO weeks
    assert stats["p"]["pts"].mean == pytest.approx(70.0)  # 7 games x 10 pts per week


# --- percentage categories ---------------------------------------------------


def test_percentage_is_volume_weighted_not_rate_ranked():
    """The bug class that made the wire ranker pick 86% shooters on 3 attempts: rank on
    contribution, not on rate. The bulk players set a realistic league rate — with only the
    two contenders in the pool, the pooled rate is whatever they make it."""
    store = Store(":memory:")
    players = {
        "volume": [_line(fgm=12, fga=20) for _ in range(28)],   # .600 on real volume
        "sniper": [_line(fgm=2, fga=3) for _ in range(28)],     # .667 on nothing
    }
    for i in range(6):  # league-average bulk: .450 on 15 attempts
        players[f"bulk{i}"] = [_line(fgm=6.75, fga=15) for _ in range(28)]
    _seed(store, players)
    vals = g_scores(store, SEASON, categories=["fg_pct"], pool_size=8)
    assert vals["volume"] > vals["sniper"]


def test_zero_attempt_week_is_zero_impact_not_undefined():
    store = Store(":memory:")
    _seed(store, {
        "a": [_line(fgm=5, fga=10) if i < 7 else _line() for i in range(14)],
        "b": [_line(fgm=5, fga=10) for _ in range(14)],
    })
    stats, _ = measure_period_stats(store, SEASON, ["fg_pct"], pool_size=2)
    assert stats["a"]["fg_pct"].periods == 2  # the empty week still counts


def test_turnovers_count_negatively():
    store = Store(":memory:")
    _seed(store, {
        "careful": [_line(pts=20, tov=1) for _ in range(28)],
        "careless": [_line(pts=20, tov=6) for _ in range(28)],
    })
    vals = g_scores(store, SEASON, categories=["pts", "tov"], pool_size=2)
    assert vals["careful"] > vals["careless"]


# --- board and knobs ---------------------------------------------------------


def test_board_is_sorted_and_breaks_down_by_category():
    store = Store(":memory:")
    _seed(store, {
        "star": [_line(pts=30, reb=10) for _ in range(28)],
        "role": [_line(pts=8, reb=3) for _ in range(28)],
    })
    board = g_score_board(store, SEASON, categories=["pts", "reb"], pool_size=2)
    assert [p for p, _, _ in board] == ["star", "role"]
    assert set(board[0][2]) == {"pts", "reb"}
    assert board[0][1] == pytest.approx(sum(board[0][2].values()), abs=1e-3)


def test_board_limit():
    store = Store(":memory:")
    _seed(store, {f"p{i}": [_line(pts=10 + i) for _ in range(28)] for i in range(5)})
    assert len(g_score_board(store, SEASON, ["pts"], pool_size=5, limit=2)) == 2


def test_kappa_flips_a_higher_mean_higher_variance_player_below_a_steady_one():
    """The decision κ actually governs: is a slightly better average worth much more
    week-to-week risk? At κ=0 (z-score) the higher average always wins; raise κ and the
    steady player passes him. This is the trade the market's metric cannot express."""
    store = Store(":memory:")
    _seed(store, {
        "steady": [_line(pts=20) for _ in range(28)],                  # 140/wk, τ≈0
        "boom": [_line(pts=42 if i % 2 else 0) for i in range(28)],    # 147/wk, τ≈21
        "replacement": [_line(pts=8) for _ in range(28)],
    })
    at_zero = g_scores(store, SEASON, ["pts"], pool_size=3, kappa=0.0)
    at_four = g_scores(store, SEASON, ["pts"], pool_size=3, kappa=4.0)
    assert at_zero["boom"] > at_zero["steady"]
    assert at_four["steady"] > at_four["boom"]


def test_kappa_sensitivity_reports_movement():
    """A-DRAFT-4: κ must be shown to matter (or not) rather than quietly fitted."""
    store = Store(":memory:")
    _seed(store, {
        "steady": [_line(pts=20) for _ in range(28)],
        "boom": [_line(pts=42 if i % 2 else 0) for i in range(28)],
        "replacement": [_line(pts=8) for _ in range(28)],
    })
    rows = kappa_sensitivity(store, SEASON, kappas=[0.0, 4.0], categories=["pts"], pool_size=3)
    assert rows[0] == (0.0, 0, 0.0)          # κ=0 is the baseline, by definition unmoved
    assert rows[1][1] > 0                     # κ=4 reorders the board


def test_empty_store_is_empty_not_an_error():
    assert g_scores(Store(":memory:"), SEASON) == {}
    assert g_score_board(Store(":memory:"), SEASON) == []


def test_basis_falls_back_to_typical_tau_for_unknown_player():
    store = Store(":memory:")
    _seed(store, {"p": [_line(pts=10) for _ in range(14)]})
    basis = xscore_basis(store, SEASON, ["pts"], pool_size=1)
    assert basis.tau_for("never-seen", "pts") == basis.bases["pts"].typical_tau
    assert basis.category_score("never-seen", "pts") == 0.0
