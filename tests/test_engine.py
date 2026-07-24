"""Decision engine: deterministic, wire-only, perspective-scoped, explainable,
and point-in-time (tasks 3.2–3.4)."""

from __future__ import annotations

from fantasy_gm.engine.engine import DecisionEngine
from fantasy_gm.models import PlayerGameLog


def _recommend(fx, team=None, top=10):
    team = team or fx.store.team_ids(fx.league_id)[0]
    return DecisionEngine().recommend(fx.store, fx.league_id, team, fx.as_of, top_n=top)


def test_returns_ranked_candidates_with_scores(fx):
    recs = _recommend(fx)
    assert recs, "expected candidates"
    assert [r.rank for r in recs] == list(range(1, len(recs) + 1))
    scores = [r.score for r in recs]
    assert scores == sorted(scores, reverse=True)


def test_only_wire_players_recommended(fx):
    team = fx.store.team_ids(fx.league_id)[0]
    recs = _recommend(fx, team=team)
    state = fx.store.league_state_asof(fx.league_id, fx.as_of)
    rostered = state.rostered_player_ids()
    assert all(r.candidate_id not in rostered for r in recs)


def test_recommendations_are_deterministic(fx):
    a = _recommend(fx)
    b = _recommend(fx)
    assert [(r.candidate_id, r.rank, r.score) for r in a] == \
           [(r.candidate_id, r.rank, r.score) for r in b]


def test_scoped_to_perspective(fx):
    team = fx.store.team_ids(fx.league_id)[1]
    recs = _recommend(fx, team=team)
    for r in recs:
        assert r.perspective.team_id == team
        assert r.perspective.opponent_team_id != team
        assert r.perspective.opponent_team_id in fx.store.team_ids(fx.league_id)


def test_reasoning_present_and_refers_to_signals(fx):
    recs = _recommend(fx)
    for r in recs:
        assert r.reasoning
        assert "game" in r.reasoning and "production" in r.reasoning
        assert 0.0 <= r.confidence <= 1.0


def test_future_data_does_not_change_recommendation(fx):
    """A game dated after D must not affect the D recommendation (point-in-time inputs)."""
    before = _recommend(fx)
    top_wire = before[0].candidate_id
    fx.store.upsert_player_logs([
        PlayerGameLog("BOOST-1", fx.season, "2099-01-01", top_wire, "boost", "AAA",
                      {"pts": 200, "reb": 99, "ast": 99, "stl": 9, "blk": 9, "tov": 0,
                       "fg3m": 9, "fg_pct": 1.0, "ft_pct": 1.0})
    ])
    after = _recommend(fx)
    assert [(r.candidate_id, r.score) for r in before] == \
           [(r.candidate_id, r.score) for r in after]
