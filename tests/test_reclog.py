"""Recommendation log: append-only integrity + reproducible-from-the-log (task 4.3)."""

from __future__ import annotations

from fantasy_gm.engine.engine import DecisionEngine
from fantasy_gm.log.reclog import RecommendationLog


def _recs(fx, team=None):
    team = team or fx.store.team_ids(fx.league_id)[0]
    return DecisionEngine().recommend(fx.store, fx.league_id, team, fx.as_of, top_n=5)


def test_append_only_no_mutation_api():
    # The log deliberately exposes no update/delete operation.
    assert not hasattr(RecommendationLog, "update")
    assert not hasattr(RecommendationLog, "delete")


def test_appending_preserves_prior_rows(fx):
    log = RecommendationLog(fx.store)
    first = _recs(fx)
    log.append(first)
    snapshot = [(r.id, r.candidate_id, r.score) for r in log.all()]
    log.append(_recs(fx))  # append a second batch
    later = log.all()
    # every original row is unchanged and still present, in order
    assert [(r.id, r.candidate_id, r.score) for r in later[: len(snapshot)]] == snapshot
    assert log.count() == 2 * len(first)


def test_record_carries_full_perspective(fx):
    log = RecommendationLog(fx.store)
    log.append(_recs(fx))
    row = log.all()[0]
    assert row.league_id == fx.league_id
    assert row.team_id and row.opponent_team_id
    assert row.period_index >= 0
    assert row.reasoning and 0.0 <= row.confidence <= 1.0


def test_reproducible_from_logged_perspective(fx):
    log = RecommendationLog(fx.store)
    original = _recs(fx)
    log.append(original)
    row = log.for_perspective(fx.league_id, original[0].perspective.team_id, fx.as_of)[0]
    # re-run the engine using only what the log records: league, team, as-of date
    replay = DecisionEngine().recommend(
        fx.store, row.league_id, row.team_id, row.as_of_date, top_n=5
    )
    assert replay[0].candidate_id == row.candidate_id
    assert replay[0].score == row.score
