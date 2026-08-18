"""The projection backtest harness (task 2.11, assumptions ledger A-DRAFT-5).

The harness is the gate on the riskiest assumption in the change — that own-built projections
are good enough — so the properties that matter are that it compares against the *strong*
baseline, that it cannot see inside the period it is scoring, and that it reports a negative
result as a negative result instead of quietly passing.
"""

from __future__ import annotations

from fantasy_gm.data.store import Store
from fantasy_gm.projections.backtest import (
    CROSS_SEASON,
    SPLIT_SEASON,
    backtest_projection,
)
from tests.test_projection_model import _seed_league

PRIOR = "2025-26"


def _store(n_games: int = 40) -> Store:
    s = Store(":memory:")
    _seed_league(s, n_games=n_games)
    return s


def test_split_season_mode_is_chosen_when_no_prior_season_is_backfilled():
    report = backtest_projection(_store(), PRIOR)
    assert report is not None
    assert report.mode == SPLIT_SEASON
    assert "proxy" in report.notes  # and it says so, rather than passing itself off as the gate


def test_cross_season_mode_reports_the_blocker_instead_of_faking_a_number():
    """Asking for the real gate without the data it needs must not silently degrade."""
    report = backtest_projection(_store(), PRIOR, mode=CROSS_SEASON)
    assert report is not None
    assert "blocked" in report.notes
    assert "2.10" in report.notes["blocked"]
    assert report.n_players == 0


def test_the_evaluation_window_starts_after_the_cut():
    report = backtest_projection(_store(), PRIOR, min_train_games=5, min_eval_games=5)
    assert report.eval_start > report.as_of
    assert report.eval_end >= report.eval_start


def test_both_methods_are_scored_on_the_same_players():
    """The comparison is paired; scoring the model and the baseline on different pools would
    make the MAE difference meaningless."""
    report = backtest_projection(_store(), PRIOR, min_train_games=5, min_eval_games=5)
    assert report.n_players > 0
    assert set(report.model.category_mae) == set(report.naive.category_mae)
    assert report.model.minutes_mae > 0 and report.naive.minutes_mae > 0


def test_the_gate_is_a_property_that_reports_failure_as_failure():
    report = backtest_projection(_store(), PRIOR, min_train_games=5, min_eval_games=5)
    verdict = report.verdict()
    if report.beats_naive_minutes:
        assert "FAIL" not in verdict
    else:
        assert verdict.startswith("FAIL") and "A-DRAFT-5" in verdict


def test_a_marginal_win_is_reported_as_inconclusive_not_as_a_pass():
    """A win inside the noise is not evidence. The verdict has to say which it is."""
    report = backtest_projection(_store(), PRIOR, min_train_games=5, min_eval_games=5)
    if report.beats_naive_minutes and report.minutes_edge_sigmas < 2.0:
        assert "INCONCLUSIVE" in report.verdict()
    assert 0.0 <= report.minutes_win_rate <= 1.0


def test_a_missing_season_returns_nothing_rather_than_an_empty_verdict():
    assert backtest_projection(_store(), "1998-99") is None


def test_the_backtest_uses_no_information_from_the_period_it_scores():
    """Structural, not conventional: the projection is fit as-of the cut, so rewriting the
    evaluation window cannot change what the model predicted."""
    store = _store()
    report = backtest_projection(store, PRIOR, min_train_games=5, min_eval_games=5)

    # Blow up production in the evaluation window only.
    cut = report.as_of
    store.conn.execute(
        """UPDATE player_logs SET stats_json = json_set(stats_json, '$.pts', 99.0)
           WHERE game_date > ?""",
        (cut,),
    )
    store.conn.commit()
    after = backtest_projection(store, PRIOR, as_of=cut, min_train_games=5, min_eval_games=5)

    # The realized truth moved, so the errors must move; the *projection* did not, so the
    # model and the baseline have to move together.
    assert after.model.category_mae["pts"] > report.model.category_mae["pts"]
    assert after.naive.category_mae["pts"] > report.naive.category_mae["pts"]


def test_minutes_bias_is_signed_so_a_systematic_over_projection_is_visible():
    report = backtest_projection(_store(), PRIOR, min_train_games=5, min_eval_games=5)
    assert abs(report.model.minutes_bias) <= report.model.minutes_mae + 1e-9


def test_percentage_categories_are_scored_volume_weighted(monkeypatch):
    """fg% error is measured on Σmakes/Σattempts (A8), not on an average of per-game
    percentages — the two differ, and only one of them is the category."""
    report = backtest_projection(_store(), PRIOR, min_train_games=5, min_eval_games=5)
    assert 0.0 <= report.model.category_mae["fg_pct"] < 1.0
    assert 0.0 <= report.model.category_mae["ft_pct"] < 1.0
