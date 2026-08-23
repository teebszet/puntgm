"""The paper-faithful simulation harness (task 3.8) and the two engine options it exercises."""

from __future__ import annotations

import random

import pytest

from fantasy_gm.data.store import Store
from fantasy_gm.draft.hscore import DraftState, HScoreEngine, _renormalise
from fantasy_gm.draft.papersim import (
    ARMS,
    WeeklyPanel,
    _titles,
    basis_from_panel,
    build_panel,
    draw_weeks,
)
from fantasy_gm.draft.settings import DraftSettings
from fantasy_gm.draft.xscore import xscore_basis
from tests.test_xscore import SEASON, _line, _seed

# --- the panel ---------------------------------------------------------------


def _panel_store() -> Store:
    store = Store(":memory:")
    # ``fragile`` plays the first week and the last, and misses everything in between — an
    # absence *inside* the active span, which is the only kind the realized basis charges for.
    _seed(store, {
        "iron": [_line(pts=20, reb=5) for _ in range(28)],
        "fragile": [_line(pts=30, reb=8) if (i < 7 or i >= 21) else None for i in range(28)],
    })
    return store


def test_panel_holds_only_weeks_the_player_played():
    """The paper resamples healthy weeks, so an absence must not appear as a zero week.

    This is the deliberate opposite of ``measure_period_stats``, which fills idle weeks with
    zeros because a realized season charges them to the manager. Availability is removed from
    this experiment on purpose — it is the one thing this project has already measured to
    death, and leaving it in would make a correctness check on the optimizer unreadable.
    """
    panel = build_panel(_panel_store(), SEASON)
    assert len(panel.weeks["iron"]) > len(panel.weeks["fragile"])
    assert all(w["pts"] > 0 for w in panel.weeks["fragile"])


def test_eligibility_is_a_week_count_floor():
    panel = build_panel(_panel_store(), SEASON)
    assert "iron" in panel.eligible(min_weeks=2)
    assert "fragile" not in panel.eligible(min_weeks=len(panel.weeks["iron"]))


def test_basis_from_panel_sees_no_idle_weeks():
    """Same store, two bases: the panel basis rates the intermittent player higher, because it
    never charges them for the weeks they missed."""
    store = _panel_store()
    panel = build_panel(store, SEASON)
    pool = ["iron", "fragile"]
    panel_basis = basis_from_panel(panel, pool)
    realized = xscore_basis(store, SEASON, pool_size=10)
    assert panel_basis.stats["fragile"]["pts"].mean > realized.stats["fragile"]["pts"].mean


# --- grading -----------------------------------------------------------------


def test_titles_split_on_a_tie_so_the_baseline_stays_one_over_n():
    assert _titles([3.0, 1.0, 1.0]) == [1.0, 0.0, 0.0]
    assert _titles([2.0, 2.0, 1.0]) == [0.5, 0.5, 0.0]
    assert sum(_titles([1.0, 1.0, 1.0, 1.0])) == pytest.approx(1.0)


def test_common_random_numbers_give_a_player_the_same_weeks_in_both_arms():
    """The estimand is a *difference* between two arms sharing eleven of twelve rosters. If a
    player's twenty weeks depended on which roster they landed on, the shared noise would not
    cancel and the difference would be swamped by it — so the draw runs over a fixed universe,
    in a fixed order, before any roster is consulted.
    """
    panel = WeeklyPanel({
        "a": [{"pts": float(i)} for i in range(10)],
        "b": [{"pts": float(i)} for i in range(10, 20)],
        "c": [{"pts": float(i)} for i in range(20, 30)],
    })
    universe = ["a", "b", "c"]
    first = draw_weeks(panel, universe, 5, random.Random(4))
    second = draw_weeks(panel, universe, 5, random.Random(4))
    assert first == second
    # Drawing only the players one arm happens to roster shifts the stream for everyone after.
    partial = draw_weeks(panel, ["b", "c"], 5, random.Random(4))
    assert partial["b"] != first["b"]


# --- the engine options the arms switch --------------------------------------


def test_renormalise_fixes_the_neutral_vector_and_holds_the_l1_norm():
    assert _renormalise([1.0] * 9, 9) == pytest.approx([1.0] * 9)
    out = _renormalise([4.0, -2.0, 0.0], 3)
    assert sum(abs(x) for x in out) == pytest.approx(3.0)
    assert _renormalise([0.0, 0.0, 0.0], 3) == [1.0, 1.0, 1.0]


def test_slice_aware_future_is_worth_less_than_the_shipped_one():
    """The shipped engine prices every remaining round as one draw from the top of the board.
    Slicing prices the j-th future pick from the pool that will still be there when it comes
    round, which must come out strictly lower for any pick after the first."""
    store = Store(":memory:")
    _seed(store, {
        f"p{i:03d}": [_line(pts=40 - i * 0.4, reb=10, ast=5, fgm=8, fga=16, ftm=4, fta=5)
                      for _ in range(21)]
        for i in range(60)
    })
    basis = xscore_basis(store, SEASON, pool_size=60)
    settings = DraftSettings(n_teams=6, rounds=5)
    board = sorted(basis.pool, key=lambda p: -basis.total(p))
    shipped = HScoreEngine(basis, settings, steps=1)
    sliced = HScoreEngine(basis, settings, steps=1, future_slices=True)

    per_pick, _ = shipped._weighted_future(board, [1.0] * 9)
    total, _ = sliced._future_block(board, [1.0] * 9, 4, settings.n_teams)
    assert total["pts"] < 4 * per_pick["pts"]
    # ...and each slice must agree exactly with a direct softmax over that same suffix, which
    # is the whole claim the suffix accumulation is making.
    for picks in (1, 2, 3):
        block_mean, block_var = sliced._future_block(board, [1.0] * 9, picks, settings.n_teams)
        direct_mean = {c: 0.0 for c in basis.categories}
        direct_var = {c: 0.0 for c in basis.categories}
        for j in range(picks):
            m, v = sliced._weighted_future(board[j * settings.n_teams:], [1.0] * 9)
            for c in basis.categories:
                direct_mean[c] += m[c]
                direct_var[c] += v[c]
        for c in basis.categories:
            assert block_mean[c] == pytest.approx(direct_mean[c], rel=1e-9, abs=1e-9)
            assert block_var[c] == pytest.approx(direct_var[c], rel=1e-9, abs=1e-9)


def test_future_pool_option_changes_what_the_engine_expects_of_a_late_pick():
    """With ``future_from_shortlist`` the remaining rounds are modelled as top-40 players; with
    it off they are modelled as the rest of the pool, which is what a draft actually leaves."""
    store = Store(":memory:")
    players = {
        f"p{i:03d}": [_line(pts=40 - i * 0.3, reb=10, ast=5, fgm=8, fga=16, ftm=4, fta=5)
                      for _ in range(21)]
        for i in range(80)
    }
    _seed(store, players)
    basis = xscore_basis(store, SEASON, pool_size=80)
    settings = DraftSettings(n_teams=4, rounds=6)
    state = DraftState(my_roster=["p000"], opponent_rosters=[["p001"], ["p002"], ["p003"]],
                       taken={"p000", "p001", "p002", "p003"})
    available = [p for p in basis.pool if p not in state.drafted()]

    shortlisted = HScoreEngine(basis, settings, steps=1, future_from_shortlist=True)
    full = HScoreEngine(basis, settings, steps=1, future_from_shortlist=False)
    short_mean, _ = shortlisted._weighted_future(available[:10], [1.0] * 9)
    full_mean, _ = full._weighted_future(available, [1.0] * 9)
    # Drawing from the whole remaining pool must not look as good as drawing from the top ten.
    assert full_mean["pts"] < short_mean["pts"]


def test_every_arm_is_a_valid_engine_configuration():
    assert set(ARMS) == {
        "g_score", "h_score", "h_full_pool", "h_normalised", "h_fullpool_normalised",
        "h_future_slices", "h_paper",
    }
    store = Store(":memory:")
    _seed(store, {f"p{i:02d}": [_line(pts=20 - i, reb=5, fgm=5, fga=10) for _ in range(21)]
                  for i in range(12)})
    basis = xscore_basis(store, SEASON, pool_size=12)
    for name, kwargs in ARMS.items():
        if name == "g_score":
            continue
        engine = HScoreEngine(basis, DraftSettings(n_teams=2, rounds=3), steps=1, **kwargs)
        assert engine.best_pick(DraftState()) is not None
