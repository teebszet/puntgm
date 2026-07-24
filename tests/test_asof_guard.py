"""Lookahead guard (tasks 2.6 / 3.4): an as-of query for date D never returns a record
known only after D — for results, availability, and roster moves."""

from __future__ import annotations

from fantasy_gm.models import Availability, Game, PlayerGameLog


def test_results_asof_excludes_future_games(fx):
    d = fx.as_of
    for g in fx.store.results_asof(d, season=fx.season):
        assert g.game_date <= d
    for lg in fx.store.player_logs_asof(d):
        assert lg.game_date <= d


def test_inserted_future_record_never_leaks(fx):
    d = fx.as_of
    future = "2099-01-01"
    fx.store.upsert_games([Game("FUT-1", fx.season, future, "AAA", "BBB", 100, 99)])
    fx.store.upsert_player_logs([
        PlayerGameLog("FUT-1", fx.season, future, "AAA-P00", "AAA Player 0", "AAA",
                      {"pts": 99, "reb": 99, "ast": 99, "stl": 9, "blk": 9, "tov": 0,
                       "fg3m": 9, "fg_pct": 1.0, "ft_pct": 1.0})
    ])
    fx.store.add_availability([
        Availability("AAA-P00", "OUT", future, "official", 1.0, "future")
    ])

    assert all(g.game_date <= d for g in fx.store.results_asof(d))
    assert all(lg.game_date <= d for lg in fx.store.player_logs_asof(d))
    # availability as-of D must not see the future OUT designation
    avail = fx.store.availability_asof("AAA-P00", d)
    assert avail is None or avail.known_from <= d


def test_future_roster_move_not_visible(fx):
    d = fx.as_of
    team = fx.store.team_ids(fx.league_id)[0]
    before = set(fx.store.roster_asof(fx.league_id, team, d))
    fx.store.add_roster_event(fx.league_id, team, "AAA-P07", "add", "2099-01-01")
    after = set(fx.store.roster_asof(fx.league_id, team, d))
    assert before == after  # future add invisible as of D


def test_schedule_is_a_priori_not_gated(fx):
    """The upcoming schedule (who plays when) is public knowledge, so it is intentionally
    visible beyond as_of — only outcomes/availability/rosters are gated."""
    upcoming = fx.store.schedule_in_window("2025-11-12", "2025-11-16", season=fx.season)
    assert any(g.game_date > fx.as_of for g in upcoming)
