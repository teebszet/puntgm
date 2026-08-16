"""Opponent model and the draft replay harness (tasks 3.5, 3.7)."""

from __future__ import annotations

import random

import pytest

from fantasy_gm.draft.opponents import (
    AdpBot,
    adp_ranks,
    picks_between,
    survival_probability,
)
from fantasy_gm.draft.replay import (
    score_rosters,
    snake_draft,
    static_order_strategy,
    weekly_totals,
)
from fantasy_gm.draft.settings import DraftSettings
from tests.test_hscore import _duplicate_pool
from tests.test_xscore import SEASON

# --- opponent model ----------------------------------------------------------


def test_survival_falls_as_more_picks_intervene():
    assert survival_probability(30.0, 2) > survival_probability(30.0, 20)
    assert survival_probability(30.0, 60) < 0.01


def test_an_early_adp_player_will_not_last():
    """The whole point of survival odds: you cannot wait on a top pick."""
    assert survival_probability(1.0, 12) < 0.1
    assert survival_probability(200.0, 12) > 0.99


def test_unranked_player_is_treated_as_available():
    assert survival_probability(None, 50) == 1.0


def test_survival_is_one_when_we_pick_next():
    assert survival_probability(5.0, 0) == 1.0


def test_snake_gap_is_asymmetric_across_seats():
    """A seat at the wheel waits nearly two rounds; a seat near the turn waits a couple of
    picks. That asymmetry is why survival matters more at some seats."""
    n = 12
    assert picks_between(0, n, 0) == 23      # first overall waits the longest
    assert picks_between(11, n, 0) == 1      # last in round 1 picks again immediately
    assert picks_between(0, n, 1) == 1
    assert picks_between(11, n, 1) == 23


def test_adp_ranks_are_one_indexed():
    assert adp_ranks(["a", "b", "c"]) == {"a": 1.0, "b": 2.0, "c": 3.0}


def test_adp_bot_picks_near_the_top_and_is_deterministic():
    order = [f"p{i}" for i in range(20)]
    picks_a = _bot_run(order, seed=3)
    picks_b = _bot_run(order, seed=3)
    assert picks_a == picks_b
    # every pick is within the reach window of the best still available
    assert all(int(p[1:]) <= i + 3 for i, p in enumerate(picks_a))


def _bot_run(order, seed):
    bot = AdpBot(order, random.Random(seed), reach=3)
    available = list(order)
    out = []
    for _ in range(6):
        p = bot.pick(available)
        out.append(p)
        available.remove(p)
    return out


def test_adp_bot_on_empty_board():
    assert AdpBot(["a"], random.Random(1)).pick([]) is None


# --- drafting ----------------------------------------------------------------


def _pool_and_settings(rounds=3, n_teams=4):
    pool = sorted(_duplicate_pool())
    settings = DraftSettings(
        categories=["pts", "reb", "blk"], n_teams=n_teams, rounds=rounds
    )
    return pool, settings


def test_snake_draft_alternates_direction_and_never_duplicates():
    pool, settings = _pool_and_settings()
    order = sorted(pool)
    strategies = [static_order_strategy(order) for _ in range(settings.n_teams)]
    rosters = snake_draft(strategies, pool, settings)

    assert all(len(r) == settings.n_rounds for r in rosters)
    picked = [p for r in rosters for p in r]
    assert len(picked) == len(set(picked))          # nobody drafted twice
    # round 1 goes seat 0 first; round 2 reverses, so seat 3 picks back-to-back
    assert rosters[0][0] == order[0]
    assert rosters[3][0] == order[3]
    assert rosters[3][1] == order[4]


def test_snake_draft_stops_cleanly_when_the_pool_runs_dry():
    pool = ["a", "b", "c"]
    settings = DraftSettings(categories=["pts"], n_teams=2, rounds=4)
    strategies = [static_order_strategy(pool) for _ in range(2)]
    rosters = snake_draft(strategies, pool, settings)
    assert sum(len(r) for r in rosters) == 3


def test_strategy_sees_its_own_roster_and_opponents():
    pool, settings = _pool_and_settings(rounds=2, n_teams=2)
    seen: list[tuple[int, int]] = []

    def spy(state, available):
        seen.append((len(state.my_roster), len(state.opponent_rosters)))
        return available[0]

    snake_draft([spy, static_order_strategy(sorted(pool))], pool, settings)
    assert seen[0] == (0, 1)      # first pick: empty roster, one opponent
    assert seen[-1][0] == 1       # by our second pick we hold one player


# --- realized grading --------------------------------------------------------


_GRADING_POOL = {
    "star1": {"pts": 28.0, "reb": 11.0, "blk": 2.4},
    "star2": {"pts": 25.0, "reb": 10.0, "blk": 2.0},
    "scrub1": {"pts": 5.0, "reb": 2.0, "blk": 0.2},
    "scrub2": {"pts": 4.0, "reb": 1.5, "blk": 0.1},
    "even1": {"pts": 15.0, "reb": 6.0, "blk": 1.0},
    "even2": {"pts": 15.0, "reb": 6.0, "blk": 1.0},
}

_SETTINGS = DraftSettings(categories=["pts", "reb", "blk"], n_teams=2, rounds=2)


def _grading_store():
    """A store with unambiguously strong and weak players, built for grading assertions."""
    from fantasy_gm.data.store import Store
    from tests.test_xscore import _line, _seed

    rng = random.Random(99)
    store = Store(":memory:")
    seeded = {
        pid: [
            _line(**{k: max(0.0, v * (1 + rng.uniform(-0.2, 0.2))) for k, v in rates.items()})
            for _ in range(28)
        ]
        for pid, rates in _GRADING_POOL.items()
    }
    _seed(store, seeded)
    return store


def test_weekly_totals_aggregate_by_week():
    totals = weekly_totals(_grading_store(), SEASON, ["pts", "reb"])
    assert totals
    any_player = next(iter(totals))
    assert all(set(w) >= {"pts", "reb"} for w in totals[any_player].values())


def test_grading_is_all_play_all_and_zero_sum():
    settings = DraftSettings(categories=["pts", "reb", "blk"], n_teams=3, rounds=2)
    rosters = [["star1", "scrub1"], ["star2", "scrub2"], ["even1", "even2"]]
    graded = score_rosters(_grading_store(), SEASON, rosters, settings)

    assert len(graded) == 3
    # every pairing is played by both sides, so wins and losses must balance exactly
    total_cat = sum(g["cat_wins"] for g in graded)
    assert total_cat == pytest.approx(sum(g["cat_games"] for g in graded) / 2)
    total_matchups = sum(g["matchup_wins"] for g in graded)
    assert total_matchups == pytest.approx(sum(g["matchups"] for g in graded) / 2)


def test_a_stronger_roster_grades_better():
    graded = score_rosters(
        _grading_store(), SEASON, [["star1", "star2"], ["scrub1", "scrub2"]], _SETTINGS
    )
    assert graded[0]["cat_wins"] > graded[1]["cat_wins"]
    assert graded[0]["matchup_wins"] > graded[1]["matchup_wins"]


def test_grading_counts_ties_as_half():
    settings = DraftSettings(categories=["pts"], n_teams=2, rounds=1)
    graded = score_rosters(_grading_store(), SEASON, [["star1"], ["star1"]], settings)
    # identical rosters tie every category every week
    assert graded[0]["cat_wins"] == pytest.approx(graded[0]["cat_games"] / 2)


def test_turnovers_grade_inverted():
    """Fewer is better: the roster producing more turnovers must lose the category."""
    from fantasy_gm.data.store import Store
    from tests.test_xscore import _line, _seed

    store = Store(":memory:")
    _seed(store, {
        "careful": [_line(pts=10, tov=1) for _ in range(14)],
        "careless": [_line(pts=10, tov=6) for _ in range(14)],
    })
    settings = DraftSettings(categories=["tov"], n_teams=2, rounds=1)
    graded = score_rosters(store, SEASON, [["careful"], ["careless"]], settings)
    assert graded[0]["cat_wins"] > graded[1]["cat_wins"]
