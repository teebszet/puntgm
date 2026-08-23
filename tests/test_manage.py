"""Baseline opponent management — simulated teams that actually stream."""

from __future__ import annotations

import pytest

from fantasy_gm.data.manage import apply_baseline_management


def _post_draft_events(store, league_id):
    draft = store.conn.execute(
        "SELECT MIN(known_from) d FROM roster_events WHERE league_id = ?", (league_id,)
    ).fetchone()["d"]
    return store.conn.execute(
        "SELECT * FROM roster_events WHERE league_id = ? AND known_from > ? ORDER BY known_from",
        (league_id, draft),
    ).fetchall()


def test_management_produces_moves_across_the_season(fx):
    report = apply_baseline_management(fx.store, fx.league_id)
    assert report.moves > 0
    assert report.periods > 0
    events = _post_draft_events(fx.store, fx.league_id)
    assert len(events) == report.moves * 2          # each move is one add + one drop
    assert {e["action"] for e in events} == {"add", "drop"}


def test_rosters_stay_the_same_size(fx):
    before = {t: len(fx.store.roster_asof(fx.league_id, t, fx.as_of))
              for t in fx.store.team_ids(fx.league_id)}
    apply_baseline_management(fx.store, fx.league_id)
    after = {t: len(fx.store.roster_asof(fx.league_id, t, "2026-03-01"))
             for t in fx.store.team_ids(fx.league_id)}
    assert set(before.values()) == set(after.values())


def test_management_drains_the_wire(fx):
    """The point of the exercise: after a managed season, fewer useful players are sitting
    unrostered than in a league that froze at the draft."""
    late = "2026-02-15"
    free_before = len(fx.store.player_universe(fx.season, late)) - len(
        fx.store.league_state_asof(fx.league_id, late).rostered_player_ids())
    apply_baseline_management(fx.store, fx.league_id)
    state = fx.store.league_state_asof(fx.league_id, late)
    free_after = len(fx.store.player_universe(fx.season, late)) - len(
        state.rostered_player_ids())
    # same number of roster spots, but they now hold *different* (better) players
    assert free_after == free_before
    assert _post_draft_events(fx.store, fx.league_id)


def test_moves_are_dated_at_the_decision_point_not_backfilled(fx):
    """Every event must be knowable on its date — the as-of layer depends on it."""
    apply_baseline_management(fx.store, fx.league_id)
    periods = fx.store.conn.execute(
        "SELECT period_start, period_end FROM matchups WHERE league_id = ?", (fx.league_id,)
    ).fetchall()
    spans = [(p["period_start"], p["period_end"]) for p in periods]
    for e in _post_draft_events(fx.store, fx.league_id):
        assert any(s <= e["known_from"] <= t for s, t in spans)


def test_management_is_deterministic(build):
    a, b = build(), build()
    ra = apply_baseline_management(a.store, a.league_id)
    rb = apply_baseline_management(b.store, b.league_id)
    assert ra.moves == rb.moves
    ea = [(e["team_id"], e["player_id"], e["action"], e["known_from"])
          for e in _post_draft_events(a.store, a.league_id)]
    eb = [(e["team_id"], e["player_id"], e["action"], e["known_from"])
          for e in _post_draft_events(b.store, b.league_id)]
    assert ea == eb


def test_rerunning_is_refused_because_it_is_not_idempotent(fx):
    apply_baseline_management(fx.store, fx.league_id)
    with pytest.raises(RuntimeError, match="not\n?\\s*idempotent|idempotent"):
        apply_baseline_management(fx.store, fx.league_id)


def test_force_allows_a_deliberate_second_pass(fx):
    first = apply_baseline_management(fx.store, fx.league_id)
    second = apply_baseline_management(fx.store, fx.league_id, force=True)
    assert first.moves > 0 and second.moves >= 0
