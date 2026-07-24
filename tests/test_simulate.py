"""Simulated leagues: reproducible from a seed, and point-in-time (task 5.3)."""

from __future__ import annotations

from fantasy_gm.data.simulate import simulate_league


def test_same_seed_yields_identical_rosters_and_matchups(build):
    a = build(league_seed=42)
    b = build(league_seed=42)
    assert a.league_id == b.league_id
    for tid in a.store.team_ids(a.league_id):
        assert a.store.roster_asof(a.league_id, tid, "2099-01-01") == \
               b.store.roster_asof(b.league_id, tid, "2099-01-01")
    ma = a.store.active_matchup(a.league_id, a.as_of)
    mb = b.store.active_matchup(b.league_id, b.as_of)
    assert (ma.team_a, ma.team_b, ma.period_index) == (mb.team_a, mb.team_b, mb.period_index)


def test_different_seed_changes_rosters(build):
    a = build(league_seed=1)
    b = build(league_seed=2)
    t = a.store.team_ids(a.league_id)[0]
    assert a.store.roster_asof(a.league_id, t, "2099-01-01") != \
        b.store.roster_asof(b.league_id, t, "2099-01-01")


def test_second_simulated_league_is_independent(fx):
    other = simulate_league(fx.store, season=fx.season, seed=99, n_teams=8, roster_size=10)
    assert other != fx.league_id
    assert fx.store.team_ids(other)


def test_tally_is_point_in_time(fx):
    """Within one matchup period the per-category tally is monotonic in as_of — it only
    ever accrues games completed on or before the date (never sees the future)."""
    # both dates fall in the same Mon–Sun period (2025-10-20 .. 2025-10-26)
    early = fx.store.league_state_asof(fx.league_id, "2025-10-22")
    later = fx.store.league_state_asof(fx.league_id, "2025-10-26")
    assert early.active_matchup and later.active_matchup
    assert early.active_matchup.period_index == later.active_matchup.period_index
    early_pts = sum(t.get("pts", 0.0) for t in early.category_tally.values())
    later_pts = sum(t.get("pts", 0.0) for t in later.category_tally.values())
    assert later_pts >= early_pts > 0.0


def test_rosters_known_from_draft(fx):
    """Rosters are dated from the draft (season start), so they are populated as of the
    first game date but a matchup tally only accrues from games played."""
    state = fx.store.league_state_asof(fx.league_id, fx.as_of)
    assert any(len(players) > 0 for players in state.rosters.values())
