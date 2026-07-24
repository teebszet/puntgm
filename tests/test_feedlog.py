"""Call-feed log (append-only) + replay scoring (move grade, projection calibration)."""

from __future__ import annotations

from fantasy_gm.engine.projection import Projector
from fantasy_gm.engine.reconcile import Reconciler
from fantasy_gm.engine.scoring import MoveGrade, calibration, grade_move
from fantasy_gm.engine.signals import SignalEngine
from fantasy_gm.log.reclog import FeedLog
from fantasy_gm.models import Perspective

MOVE_DATE = "2025-11-14"


def _persp(fx, team="T00"):
    m = fx.store.matchup_for_team(fx.league_id, team, MOVE_DATE)
    opp = m.team_b if m.team_a == team else m.team_a
    return Perspective(fx.league_id, team, m.period_index, opp)


def test_feedlog_is_append_only():
    assert not hasattr(FeedLog, "update")
    assert not hasattr(FeedLog, "delete")


def test_append_signals_and_moves(fx):
    log = FeedLog(fx.store)
    sigs = SignalEngine().detect(fx.store, fx.league_id, "T00", MOVE_DATE)
    moves = Reconciler().reconcile(fx.store, fx.league_id, "T00", MOVE_DATE)
    log.append_signals(sigs, _persp(fx))
    log.append_moves(moves)
    assert log.signal_count() == len(sigs)
    assert log.move_count() == len(moves)
    row = log.moves()[0]
    assert row["league_id"] == fx.league_id and row["team_id"] == "T00"
    assert row["opponent_team_id"] and row["line_of_play"]


def test_appending_preserves_prior_rows(fx):
    log = FeedLog(fx.store)
    moves = Reconciler().reconcile(fx.store, fx.league_id, "T00", MOVE_DATE)
    log.append_moves(moves)
    first = [(r["id"], r["add_id"], r["drop_id"]) for r in log.moves()]
    log.append_moves(moves)
    later = [(r["id"], r["add_id"], r["drop_id"]) for r in log.moves()]
    assert later[: len(first)] == first
    assert log.move_count() == 2 * len(moves)


def test_move_is_graded_by_realized_impact(fx):
    moves = Reconciler().reconcile(fx.store, fx.league_id, "T00", MOVE_DATE)
    assert moves
    grade = grade_move(fx.store, moves[0])
    assert isinstance(grade, MoveGrade)
    assert isinstance(grade.target_category, str) and grade.target_category
    assert isinstance(grade.net_categories, int)
    assert isinstance(grade.flipped_to_me, list) and isinstance(grade.flipped_away, list)


def test_projection_calibration_is_measurable(fx):
    proj = Projector().project(fx.store, fx.league_id, "T00", MOVE_DATE)
    cal = calibration(fx.store, proj)
    assert cal["safe_total"] >= 0
    assert 0 <= cal["safe_held"] <= cal["safe_total"]
