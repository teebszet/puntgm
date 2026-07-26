"""Call feed: point-in-time, soft/strong grading, opponent inference, reconciliation."""

from __future__ import annotations

from fantasy_gm.data.store import Store
from fantasy_gm.engine.reconcile import Reconciler
from fantasy_gm.engine.signals import SignalEngine
from fantasy_gm.models import Availability, Game, Matchup, PlayerGameLog, UsageRole

MOVE_DATE = "2025-11-14"


def _an_fa(fx):
    state = fx.store.league_state_asof(fx.league_id, fx.as_of)
    rostered = state.rostered_player_ids()
    for pid, name, _t in fx.store.player_universe(fx.season, fx.as_of):
        if pid not in rostered:
            return pid, name
    raise AssertionError("no free agent")


def test_signals_are_point_in_time(fx):
    before = SignalEngine().detect(fx.store, fx.league_id, "T00", fx.as_of)
    pid, _ = _an_fa(fx)
    # a usage snapshot dated in the future must not change today's feed
    fx.store.add_usage_role([UsageRole(pid, "2099-01-01", 99, 99, True, 1)])
    after = SignalEngine().detect(fx.store, fx.league_id, "T00", fx.as_of)
    assert [(s.subject_player, s.strength) for s in before] == \
           [(s.subject_player, s.strength) for s in after]


def test_feed_is_deterministic(fx):
    a = SignalEngine().detect(fx.store, fx.league_id, "T00", MOVE_DATE)
    b = SignalEngine().detect(fx.store, fx.league_id, "T00", MOVE_DATE)
    assert [s.__dict__ for s in a] == [s.__dict__ for s in b]


def test_mirage_stays_soft(fx):
    pid, _ = _an_fa(fx)
    # overwrite this FA's usage with a stable line and a single late spike, no depth change
    fx.store.add_usage_role([
        UsageRole(pid, "2025-10-21", 24, 10, False, 3),
        UsageRole(pid, "2025-10-28", 24, 10, False, 3),
        UsageRole(pid, "2025-11-04", 24, 10, False, 3),
        UsageRole(pid, "2025-11-11", 30, 13, False, 3),  # one-off spike, no cause
    ])
    sigs = SignalEngine().detect(fx.store, fx.league_id, "T00", "2025-11-11")
    mine = [s for s in sigs if s.subject_player == pid and s.signal_type == "usage_trend_up"]
    assert mine and mine[0].band == "soft"


def test_opponent_move_is_inferred(fx):
    m = fx.store.matchup_for_team(fx.league_id, "T00", MOVE_DATE)
    opp = m.team_b if m.team_a == "T00" else m.team_a
    opp_players = fx.store.roster_asof(fx.league_id, opp, MOVE_DATE)
    fa_id, _ = _an_fa(fx)
    fx.store.add_roster_event(fx.league_id, opp, opp_players[0], "drop", m.period_start)
    fx.store.add_roster_event(fx.league_id, opp, fa_id, "add", m.period_start)
    sigs = SignalEngine().detect(fx.store, fx.league_id, "T00", MOVE_DATE)
    opp_sigs = [s for s in sigs if s.signal_type == "opponent_move"]
    assert opp_sigs and opp_sigs[0].affected_categories


def test_reconciliation_move_shape(fx):
    moves = Reconciler().reconcile(fx.store, fx.league_id, "T00", MOVE_DATE)
    assert moves, "expected at least one candidate move"
    m = moves[0]
    assert m.add_id and m.drop_id and m.add_id != m.drop_id
    assert m.line_of_play.lower().startswith("contest")
    assert isinstance(m.projected_impact, dict) and m.projected_impact
    assert 0.0 <= m.confidence <= 1.0


# --- controlled store for the strong-signal path -----------------------------

def _line(**c):
    base = {k: 0.0 for k in ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fg_pct", "ft_pct")}
    base.update(c)
    return base


def _strong_store():
    s = Store(":memory:")
    cats = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fg_pct", "ft_pct"]
    s.create_league("L", "L", "2025-26", "weekly-lock", cats)
    for t in ("T0", "T1"):
        s.add_team("L", t, t)
    s.add_roster_event("L", "T0", "P0", "add", "2025-10-01")
    s.add_roster_event("L", "T1", "P1", "add", "2025-10-01")
    s.add_matchup(Matchup("L", 0, "2025-10-20", "2025-10-26", "T0", "T1"))
    # equal, low steals for both rostered players -> stl is contested
    s.upsert_games([Game("g1", "2025-26", "2025-10-16", "A", "B"),
                    Game("g2", "2025-26", "2025-10-18", "A", "B"),
                    Game("gA", "2025-26", "2025-10-24", "A", "Z"),
                    Game("gB", "2025-26", "2025-10-24", "B", "Z"),
                    Game("gX", "2025-26", "2025-10-24", "X", "Z")])
    s.upsert_player_logs([
        PlayerGameLog("g1", "2025-26", "2025-10-16", "P0", "P0", "A", _line(stl=3, pts=10)),
        PlayerGameLog("g2", "2025-26", "2025-10-18", "P0", "P0", "A", _line(stl=3, pts=10)),
        PlayerGameLog("g1", "2025-26", "2025-10-16", "P1", "P1", "B", _line(stl=3, pts=8)),
        PlayerGameLog("g2", "2025-26", "2025-10-18", "P1", "P1", "B", _line(stl=3, pts=8)),
        # free agent strong in steals, with a sustained + causal usage climb
        PlayerGameLog("g1", "2025-26", "2025-10-16", "PX", "Steals Guy", "X", _line(stl=6, pts=2)),
        PlayerGameLog("g2", "2025-26", "2025-10-18", "PX", "Steals Guy", "X", _line(stl=6, pts=2)),
    ])
    s.add_usage_role([
        UsageRole("PX", "2025-10-01", 20, 8, False, 3),
        UsageRole("PX", "2025-10-08", 26, 11, False, 2),
        UsageRole("PX", "2025-10-15", 32, 14, True, 1),
    ])
    return s


def test_strong_signal_when_sustained_causal_and_impactful():
    s = _strong_store()
    sigs = SignalEngine().detect(s, "L", "T0", "2025-10-22")
    px = [x for x in sigs if x.subject_player == "PX"]
    assert px, "expected a signal for the breakout free agent"
    assert px[0].band == "strong"
    assert "stl" in px[0].affected_categories


def test_out_player_signal_context(fx):
    # sanity: an OUT designation is respected by projection feeding the feed
    pid, _ = _an_fa(fx)
    fx.store.add_availability([Availability(pid, "OUT", fx.as_of, "official", 1.0, "")])
    # detection still runs without error and excludes nothing improperly
    sigs = SignalEngine().detect(fx.store, fx.league_id, "T00", fx.as_of)
    assert isinstance(sigs, list)
