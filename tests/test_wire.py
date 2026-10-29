"""Wire availability analysis (A9): per-cat trade-off verdicts + bundle depth."""

from __future__ import annotations

from fantasy_gm.engine.wire import BUNDLES, WireAnalyzer

MOVE_DATE = "2025-11-14"


def test_analysis_has_option_per_contested_cat(fx):
    from fantasy_gm.engine.projection import Projector
    wa = WireAnalyzer().analyze(fx.store, fx.league_id, "T00", MOVE_DATE)
    contested = Projector().project(fx.store, fx.league_id, "T00", MOVE_DATE).contested()
    assert {o.category for o in wa.options} == set(contested)
    for o in wa.options:
        assert o.verdict in ("chase", "trade-off", "infeasible")
        assert isinstance(o.concedes, dict)
    assert wa.perspective.team_id == "T00" and wa.perspective.opponent_team_id


def test_feasible_option_names_a_real_add_and_gains(fx):
    wa = WireAnalyzer().analyze(fx.store, fx.league_id, "T00", MOVE_DATE)
    for o in wa.options:
        if o.verdict != "infeasible":
            assert o.add_id and o.add_name
            assert o.gain > 0.0
            # concedes only lists negative deltas
            assert all(d < 0 for d in o.concedes.values())


def test_bundle_depth_partitions_the_wire(fx):
    wa = WireAnalyzer().analyze(fx.store, fx.league_id, "T00", MOVE_DATE)
    assert set(wa.bundle_depth) == set(BUNDLES)
    # every classified player lands in exactly one bundle; total <= available wire size
    rostered = fx.store.league_state_asof(fx.league_id, MOVE_DATE).rostered_player_ids()
    wire_size = sum(1 for p, _n, _t in fx.store.player_universe(fx.season, MOVE_DATE)
                    if p not in rostered)
    assert 0 <= sum(wa.bundle_depth.values()) <= wire_size


def test_drop_is_the_lowest_valued_roster_player(fx):
    """The digest prints "every swap drops X" — X must be the roster player with the
    lowest current-season value, and it must resolve to a display name, not a bare id."""
    from fantasy_gm.engine.wire import player_values
    wa = WireAnalyzer().analyze(fx.store, fx.league_id, "T00", MOVE_DATE)
    roster = fx.store.league_state_asof(fx.league_id, MOVE_DATE).rosters.get("T00", [])
    values = player_values(fx.store, fx.season)
    lowest = min(roster, key=lambda p: values.get(p, -999.0))
    assert wa.drop_id == lowest
    assert wa.drop_name == next(n for p, n, _t in fx.store.player_universe(fx.season, MOVE_DATE)
                                if p == lowest)
