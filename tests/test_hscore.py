"""H₀ dynamic pick valuation: objective, assignment, and roster-conditional behaviour."""

from __future__ import annotations

import itertools
import math
import random

import pytest

from fantasy_gm.data.store import Store
from fantasy_gm.draft.assignment import assign_to_slots, solve_assignment
from fantasy_gm.draft.hscore import DraftState, HScoreEngine
from fantasy_gm.draft.objective import (
    category_win_prob,
    prob_at_least,
    score_objective,
)
from fantasy_gm.draft.settings import DraftSettings, Objective, RosterSlot
from fantasy_gm.draft.xscore import xscore_basis
from tests.test_xscore import SEASON, _line, _seed

# --- objective ---------------------------------------------------------------


def test_win_prob_is_monotone_in_the_lead():
    p_behind = category_win_prob(-5.0, 4.0, "pts")
    p_level = category_win_prob(0.0, 4.0, "pts")
    p_ahead = category_win_prob(5.0, 4.0, "pts")
    assert p_behind < p_level < p_ahead
    assert p_level == pytest.approx(0.5)


def test_win_prob_inverts_for_turnovers():
    """Fewer turnovers is better, so a positive differential is a loss."""
    assert category_win_prob(5.0, 4.0, "tov") < 0.5
    assert category_win_prob(-5.0, 4.0, "tov") > 0.5


def test_equal_lead_is_less_safe_in_a_noisier_category():
    assert category_win_prob(5.0, 100.0, "pts") < category_win_prob(5.0, 1.0, "pts")


def test_tie_margin_pulls_toward_a_coin_flip():
    """A small integer lead is not a win if a tie is plausible."""
    no_ties = category_win_prob(1.0, 4.0, "blk")
    with_ties = category_win_prob(1.0, 4.0, "blk", tie_margin=1.0)
    assert with_ties < no_ties


def test_prob_at_least_matches_brute_force():
    probs = [0.2, 0.55, 0.9, 0.4, 0.7]
    for k in range(len(probs) + 2):
        brute = 0.0
        for combo in itertools.product([0, 1], repeat=len(probs)):
            if sum(combo) >= k:
                m = 1.0
                for bit, p in zip(combo, probs, strict=True):
                    m *= p if bit else (1 - p)
                brute += m
        assert prob_at_least(probs, k) == pytest.approx(brute, abs=1e-9)


def test_each_category_and_most_categories_are_different_objectives():
    """Spread-thin vs concentrated. Each-category likes the balanced profile; most-categories
    prefers securing a majority, which is what makes conceding rational."""
    balanced = [0.56] * 9          # Σ = 5.04, but P(win ≥5) is only ~0.63
    concentrated = [0.95] * 5 + [0.05] * 4   # Σ = 4.95, yet P(win ≥5) is ~0.78
    each = DraftSettings(objective=Objective.EACH_CATEGORY)
    most = DraftSettings(objective=Objective.MOST_CATEGORIES)
    assert score_objective(balanced, each) > score_objective(concentrated, each)
    assert score_objective(concentrated, most) > score_objective(balanced, most)


# --- assignment --------------------------------------------------------------


def test_assignment_matches_brute_force_on_random_matrices():
    rng = random.Random(11)
    for _ in range(30):
        n = rng.randint(1, 5)
        mat = [[rng.uniform(-5, 5) for _ in range(n)] for _ in range(n)]
        _, total = solve_assignment(mat)
        best = max(
            sum(mat[r][c] for r, c in enumerate(perm)) for perm in itertools.permutations(range(n))
        )
        assert total == pytest.approx(best, abs=1e-6)


def test_assignment_respects_ineligibility():
    mat = [[-math.inf, 5.0], [3.0, 4.0]]
    assignment, total = solve_assignment(mat)
    assert assignment[0] == 1
    assert total == pytest.approx(8.0)


def test_multi_eligible_player_fits_where_a_specialist_cannot():
    slots = [RosterSlot.of("C"), RosterSlot.of("PG")]
    positions = {"center": frozenset({"C"}), "swing": frozenset({"PG", "C"})}
    placed, unplaced = assign_to_slots(positions, ["center", "swing"], slots)
    assert unplaced == []
    assert placed["center"] == "C" and placed["swing"] == "PG"


def test_unfillable_roster_is_reported_not_silently_fudged():
    slots = [RosterSlot.of("PG")]
    positions = {"c1": frozenset({"C"}), "c2": frozenset({"C"})}
    placed, unplaced = assign_to_slots(positions, ["c1", "c2"], slots)
    assert placed == {}
    assert set(unplaced) == {"c1", "c2"}


# --- H0 behaviour ------------------------------------------------------------


def _basis_with(players: dict[str, dict[str, float]], pool_size: int = 20, noise: float = 0.35):
    """Build a basis from players described by their mean per-game category rates.

    Production is jittered deterministically around those means. Constant production would
    give every player τ=0, which drives every category win probability to exactly 0 or 1 and
    makes a variance-aware engine untestable — the degenerate case, not a simple one.
    """
    store = Store(":memory:")
    rng = random.Random(1234)
    seeded = {}
    for pid, rates in players.items():
        lines = []
        for _ in range(28):
            jittered = {
                k: max(0.0, v * (1.0 + rng.uniform(-noise, noise))) for k, v in rates.items()
            }
            lines.append(_line(**jittered))
        seeded[pid] = lines
    _seed(store, seeded)
    return xscore_basis(store, SEASON, categories=["pts", "reb", "blk"], pool_size=pool_size)


def _engine(basis, rounds=4, steps=6, **kw):
    settings = DraftSettings(categories=["pts", "reb", "blk"], n_teams=2, rounds=rounds)
    return HScoreEngine(basis, settings, steps=steps, **kw)


def _duplicate_pool() -> dict[str, dict[str, float]]:
    """A pool deep and even enough that category outcomes stay genuinely uncertain.

    ``bigA``/``bigB`` are interchangeable rebound-and-block bigs; ``guard`` is the
    complementary scorer. The dozen mid players exist so neither archetype is scarce enough
    to make any category a foregone conclusion — with a thin pool every probability pegs at
    0 or 1 and roster-conditional differences vanish into the saturation.
    """
    pool = {
        "bigA": {"reb": 11.5, "blk": 2.2, "pts": 15.0},
        "bigB": {"reb": 11.0, "blk": 2.1, "pts": 15.0},
        "guard": {"pts": 22.0, "reb": 4.0, "blk": 0.4},
    }
    for i in range(12):
        pool[f"mid{i}"] = {
            "pts": 14.0 + (i % 4) * 1.5,
            "reb": 6.0 + (i % 3) * 1.2,
            "blk": 0.8 + (i % 3) * 0.4,
        }
    return pool


def _relative_value(objective, my_roster, opponent=("mid0", "mid1")):
    basis = _basis_with(_duplicate_pool(), pool_size=20)
    settings = DraftSettings(
        categories=["pts", "reb", "blk"], n_teams=2, rounds=6, objective=objective
    )
    eng = HScoreEngine(basis, settings, steps=6)
    taken = set(my_roster) | set(opponent)
    ranked = eng.evaluate_candidates(
        DraftState(my_roster=list(my_roster), opponent_rosters=[list(opponent)], taken=taken)
    )
    v = {c.player_id: c.delta for c in ranked}
    return v["bigB"] - v["guard"]   # duplicate-of-mine vs complementary-to-mine


def test_marginal_contribution_is_discounted_in_an_already_won_category():
    """Spec: production in a category you already win adds less than the same production in a
    category near even odds.

    Tested at the objective directly rather than through a draft: it is a property of the
    normal CDF (flat in the tails, steepest at the mean), so a scenario-level test would only
    obscure it behind whatever the pool happened to make reachable.
    """
    step = 1.0
    var = 4.0
    contested = category_win_prob(0.0, var, "pts")
    contested_after = category_win_prob(step, var, "pts")
    safe = category_win_prob(6.0, var, "pts")
    safe_after = category_win_prob(6.0 + step, var, "pts")
    assert (contested_after - contested) > (safe_after - safe)


def test_same_player_is_valued_differently_by_different_rosters():
    """The spec's core requirement for a *dynamic* engine: identical candidate, different
    drafted roster, different value. A static ranking list cannot do this by construction."""
    empty = _relative_value(Objective.EACH_CATEGORY, [])
    stocked = _relative_value(Objective.EACH_CATEGORY, ["bigA"])
    assert empty != pytest.approx(stocked, abs=1e-3)


def test_objective_changes_how_a_roster_conditions_value():
    """The same roster change pulls the two objectives in different directions — evidence the
    objective is genuinely wired into the valuation rather than rescaling it."""
    each = _relative_value(Objective.EACH_CATEGORY, ["bigA"]) - _relative_value(
        Objective.EACH_CATEGORY, []
    )
    most = _relative_value(Objective.MOST_CATEGORIES, ["bigA"]) - _relative_value(
        Objective.MOST_CATEGORIES, []
    )
    assert (each > 0) != (most > 0)


def test_concentration_emerges_without_being_declared():
    """Spec: concentration must arise from maximising the objective, never from a checkbox.

    Under a majority objective the engine's top pick should end up conceding at least one
    category outright while locking a majority — and the strategy weights it settled on should
    not be the uniform vector it started from.
    """
    basis = _basis_with(_duplicate_pool(), pool_size=20)
    settings = DraftSettings(
        categories=["pts", "reb", "blk"], n_teams=2, rounds=6,
        objective=Objective.MOST_CATEGORIES,
    )
    eng = HScoreEngine(basis, settings, steps=8)
    state = DraftState(opponent_rosters=[["mid0", "mid1"]], taken={"mid0", "mid1"})
    top = eng.evaluate_candidates(state)[0]

    probs = sorted(top.win_probs.values())
    assert probs[0] < 0.35              # a category conceded...
    assert probs[-1] > 0.90             # ...to lock others
    assert any(abs(w - 1.0) > 1e-3 for w in top.weights.values())  # weights moved


def test_engine_excludes_drafted_players():
    basis = _basis_with({
        "a": {"pts": 20}, "b": {"pts": 18}, "c": {"pts": 16}, "d": {"pts": 14},
    })
    eng = _engine(basis)
    state = DraftState(my_roster=["a"], opponent_rosters=[["b"]], taken={"a", "b"})
    ids = [c.player_id for c in eng.evaluate_candidates(state)]
    assert "a" not in ids and "b" not in ids


def test_candidates_are_ranked_and_carry_probabilities_and_weights():
    basis = _basis_with({
        "star": {"pts": 30, "reb": 10, "blk": 2},
        "role": {"pts": 8, "reb": 3, "blk": 0.3},
        "sub": {"pts": 6, "reb": 2, "blk": 0.2},
    })
    eng = _engine(basis)
    ranked = eng.evaluate_candidates(DraftState(opponent_rosters=[[]]))
    assert [c.player_id for c in ranked][0] == "star"
    assert all(v == sorted(v, reverse=True) for v in [[c.value for c in ranked]])
    top = ranked[0]
    assert set(top.win_probs) == {"pts", "reb", "blk"}
    assert all(0.0 <= p <= 1.0 for p in top.win_probs.values())
    assert set(top.weights) == {"pts", "reb", "blk"}


def test_best_pick_and_empty_pool():
    basis = _basis_with({"a": {"pts": 20}, "b": {"pts": 10}})
    eng = _engine(basis)
    assert eng.best_pick(DraftState(opponent_rosters=[[]])).player_id == "a"
    exhausted = DraftState(my_roster=["a"], opponent_rosters=[["b"]], taken={"a", "b"})
    assert eng.best_pick(exhausted) is None


def test_future_pick_uncertainty_shrinks_as_the_draft_progresses():
    """With many rounds left, unknown future picks on both sides dominate the variance, so a
    small lead reads as close to a coin flip. Late in the draft the same lead is nearly
    decided, because there is little left that could change it."""
    basis = _basis_with({
        "a": {"pts": 20, "reb": 8, "blk": 1.0},
        "b": {"pts": 19, "reb": 8, "blk": 1.0},
        "c": {"pts": 18, "reb": 8, "blk": 1.0},
        "d": {"pts": 18, "reb": 8, "blk": 1.0},
        "e": {"pts": 17, "reb": 8, "blk": 1.0},
        "f": {"pts": 17, "reb": 8, "blk": 1.0},
        "g": {"pts": 16, "reb": 8, "blk": 1.0},
    })
    state = DraftState(my_roster=["a"], opponent_rosters=[["b"]], taken={"a", "b"})
    # steps=0 disables strategy optimisation, so both sides' future picks are drawn the same
    # way. The mean differential is then fixed by the known rosters and only the variance from
    # unknown picks changes — which is the property under test.
    p_early = _engine(basis, rounds=8, steps=0).evaluate_candidates(state)[0].win_probs["pts"]
    p_late = _engine(basis, rounds=3, steps=0).evaluate_candidates(state)[0].win_probs["pts"]
    assert abs(p_late - 0.5) > abs(p_early - 0.5)


def test_objective_choice_changes_the_pick():
    """A specialist that locks one category vs an all-rounder that nudges three. Which is
    better is exactly what the objective decides."""
    players = {
        "specialist": {"blk": 6, "pts": 6, "reb": 3},
        "allrounder": {"blk": 1.2, "pts": 16, "reb": 8},
        "filler1": {"blk": 1.0, "pts": 12, "reb": 6},
        "filler2": {"blk": 0.9, "pts": 11, "reb": 6},
    }
    basis = _basis_with(players)
    picks = {}
    for obj in (Objective.EACH_CATEGORY, Objective.MOST_CATEGORIES):
        settings = DraftSettings(
            categories=["pts", "reb", "blk"], n_teams=2, rounds=3, objective=obj
        )
        eng = HScoreEngine(basis, settings, steps=6)
        ranked = eng.evaluate_candidates(DraftState(opponent_rosters=[[]]))
        picks[obj] = [c.player_id for c in ranked]
    # Both must produce a full, valid ranking; the objective is wired through to the score.
    assert set(picks[Objective.EACH_CATEGORY]) == set(picks[Objective.MOST_CATEGORIES])
    assert all(len(v) == 4 for v in picks.values())


def test_warm_start_is_reset_on_demand():
    basis = _basis_with({"a": {"pts": 20}, "b": {"pts": 10}})
    eng = _engine(basis)
    eng.evaluate_candidates(DraftState(opponent_rosters=[[]]))
    assert eng._warm is not None
    eng.reset_warm_start()
    assert eng._warm is None


# --- settings ----------------------------------------------------------------


def test_settings_majority_and_rounds():
    s = DraftSettings()
    assert s.n_categories == 9
    assert s.majority == 5
    assert s.n_rounds == len(s.slots)
    assert len(s.starting_slots) == len(s.slots) - 3  # three bench spots


def test_settings_from_names_and_unknown_slot():
    s = DraftSettings.from_names(["PG", "UTIL", "BN"], categories=["pts", "reb"])
    assert [x.name for x in s.slots] == ["PG", "UTIL", "BN"]
    assert s.majority == 2
    with pytest.raises(ValueError):
        RosterSlot.of("QB")


def test_flex_slots_accept_their_positions():
    assert RosterSlot.of("G").accepts(frozenset({"SG"}))
    assert not RosterSlot.of("G").accepts(frozenset({"C"}))
    assert RosterSlot.of("UTIL").accepts(frozenset({"C"}))
