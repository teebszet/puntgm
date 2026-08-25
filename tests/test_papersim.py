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
    run_paper_sim,
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


def test_idle_weeks_option_fills_the_gap_inside_the_active_span():
    """``include_idle_weeks=True`` puts availability back into the paper room — the one axis on
    which it and the replay room disagree, and therefore the one that has to be switchable
    rather than argued about (see ``scripts/room_decomposition.py``)."""
    panel = build_panel(_panel_store(), SEASON, include_idle_weeks=True)
    # Same span, same length: the gap is filled, not the player dropped.
    assert len(panel.weeks["fragile"]) == len(panel.weeks["iron"])
    # An idle week is an empty line. Every reader takes components with ``.get(k, 0.0)``, so
    # that is a genuine zero rather than a missing observation.
    assert any(w == {} for w in panel.weeks["fragile"])
    assert not any(w == {} for w in panel.weeks["iron"])


def test_idle_weeks_do_not_buy_eligibility():
    """The paper's inclusion rule is ten *played* weeks. Padding must not smuggle a
    twelve-week absentee into the pool as a qualified player."""
    floor = len(build_panel(_panel_store(), SEASON).weeks["iron"])
    padded = build_panel(_panel_store(), SEASON, include_idle_weeks=True)
    assert len(padded.weeks["fragile"]) >= floor
    assert "fragile" not in padded.eligible(min_weeks=floor)


def test_idle_panel_reproduces_the_realized_basis():
    """The point of the flag is that it recreates the replay room's semantics exactly, not
    approximately — otherwise a cell of the decomposition would be measuring a third thing."""
    store = _panel_store()
    pool = ["iron", "fragile"]
    padded = basis_from_panel(build_panel(store, SEASON, include_idle_weeks=True), pool)
    realized = xscore_basis(store, SEASON, pool_size=10)
    for pid in pool:
        assert padded.stats[pid]["pts"].mean == pytest.approx(realized.stats[pid]["pts"].mean)
        assert padded.stats[pid]["pts"].std == pytest.approx(realized.stats[pid]["pts"].std)


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


# --- the field the arm drafts against ----------------------------------------


def test_unknown_field_is_rejected():
    with pytest.raises(ValueError, match="unknown field"):
        run_paper_sim(_panel_store(), SEASON, field="whatever")


def test_the_null_is_only_worth_chance_against_its_own_field():
    """The two fields differ in exactly the way that makes the null non-optional.

    In a G-score field the null is a thirteenth copy of a board every other seat is already
    running, so pooled over every seat its title rate is **exactly** 1/N — titles sum to one
    per season and no seat has an edge in strategy. That identity is what lets a paper-room
    H₀ row be read at all.

    In an ADP field the same null is a *better drafter than its opponents*, so it wins far more
    than its share. Comparing an ADP-field row to the 1/N chance baseline would therefore credit
    the board's advantage over bots to the engine. Differencing against this null is the only
    thing that removes it.
    """
    store = Store(":memory:")
    _seed(store, {
        # Seventy game-days, because the panel's inclusion rule is ten *weeks* and ``_seed``
        # lays one line per day — a twenty-one-game fixture spans three weeks and empties the
        # pool.
        f"p{i:02d}": [_line(pts=30 - i, reb=6, ast=3, fgm=6, fga=12, ftm=3, fta=4)
                      for _ in range(70)]
        for i in range(24)
    })
    kw = dict(
        season=SEASON, arm="g_score", n_seasons=20, seed=3,
        n_teams=4, n_rounds=3, pool_size=24,
    )
    chance = 1.0 / 4
    assert run_paper_sim(store, **kw).mean_title_rate == pytest.approx(chance)
    assert run_paper_sim(store, **kw, field="adp").mean_title_rate > chance


def test_the_field_stream_does_not_depend_on_the_arm():
    """Common random numbers across arms is what the whole harness rests on. If the bots' draws
    were seeded from the arm name, the null and the arm would face different fields and the
    difference between them would be measuring the field instead of the engine."""
    store = Store(":memory:")
    _seed(store, {
        # Seventy game-days, because the panel's inclusion rule is ten *weeks* and ``_seed``
        # lays one line per day — a twenty-one-game fixture spans three weeks and empties the
        # pool.
        f"p{i:02d}": [_line(pts=30 - i, reb=6, ast=3, fgm=6, fga=12, ftm=3, fta=4)
                      for _ in range(70)]
        for i in range(24)
    })
    kw = dict(
        season=SEASON, n_seasons=2, seats=[0], seed=3, n_teams=4, n_rounds=3,
        pool_size=24, field="adp",
    )
    first = run_paper_sim(store, arm="g_score", **kw)
    second = run_paper_sim(store, arm="g_score", **kw)
    assert first.seats[0].cat_win_rate == second.seats[0].cat_win_rate
