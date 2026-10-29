"""The injury usage-spike watch: who absorbs an injured teammate's minutes?"""

from __future__ import annotations

from datetime import date, timedelta

from fantasy_gm.data.synthetic import TEAMS
from fantasy_gm.engine.signals import injury_usage_spikes
from fantasy_gm.models import Availability, PlayerPosition, UsageRole

AS_OF = "2025-11-11"  # the fixture's mid-season date


def _usage(store, pid: str, minutes: float, known_from: str = "2025-11-07") -> None:
    # five dated rows ending ON as_of (2025-11-11): the synthetic seed already
    # snapshots weekly, and the watch averages the LAST 5 rows, so these must be the
    # most recent ones — known_from 11-07..11-11 replaces the synthetic 11-11 row.
    store.add_usage_role([
        UsageRole(pid, (date.fromisoformat(known_from) + timedelta(days=d)).isoformat(),
                  minutes, 10.0, False, 3)
        for d in range(5)
    ])


def _fresh_out(store, pid: str, known_from: str = "2025-11-10") -> None:
    store.add_availability([Availability(pid, "OUT", known_from, "official", 0.95, "test")])


def _victim(fx) -> str:
    """A rostered player with a controlled recent minutes level."""
    state = fx.store.league_state_asof(fx.league_id, AS_OF)
    return sorted(state.rostered_player_ids())[0]


def test_fresh_out_rostered_player_watches_same_team_candidates(fx):
    store, victim = fx.store, _victim(fx)
    # victim's own NBA team from his logged games; seed a same-team bench corps
    victim_team = next(t for t in TEAMS if victim.startswith(t))
    teammates = [f"{victim_team}-P10", f"{victim_team}-P11", f"{victim_team}-P12",
                 f"{victim_team}-P13"]

    _usage(store, victim, 30.0)
    _fresh_out(store, victim)
    store.add_player_positions([
        PlayerPosition(victim, "F", "2025-10-01"),
        PlayerPosition(teammates[0], "F", "2025-10-01"),   # same slot, big gap -> candidate
        PlayerPosition(teammates[1], "F", "2025-10-01"),   # same slot, gap < 8 -> skipped
        PlayerPosition(teammates[2], "F", "2025-10-01"),   # same slot but OUT -> skipped
        PlayerPosition(teammates[3], "G", "2025-10-01"),   # different slot -> skipped
    ])
    _usage(store, teammates[0], 10.0)
    _usage(store, teammates[1], 25.0)
    _usage(store, teammates[2], 10.0)
    _usage(store, teammates[3], 5.0)
    _fresh_out(store, teammates[2])  # already-OUT teammate cannot absorb minutes

    watch = injury_usage_spikes(store, fx.league_id, AS_OF)
    spike = next(s for s in watch.spikes if s.injured_player_id == victim)
    assert spike.injured_team == victim_team
    assert spike.injured_minutes == 30.0
    assert [c.player_id for c in spike.candidates] == [teammates[0]]
    assert spike.candidates[0].minutes_gap == 20.0


def test_names_resolve_across_seasons_when_a_newer_season_has_fewer_players(fx):
    """A player whose only box rows are last season must still show his name: the
    backfill of a fresh season shrinks the per-season universe but not the names map."""
    store, victim = fx.store, _victim(fx)
    _usage(store, victim, 30.0)
    _fresh_out(store, victim)
    store.add_player_positions([PlayerPosition(victim, "F", "2025-10-01")])

    # a 2026-27 log for one new player makes latest_logged_season() = 2026-27
    from fantasy_gm.models import Game, PlayerGameLog
    store.upsert_games([Game("G26-0001", "2026-27", "2026-10-21", "ZZZ", "AAA", 100, 90)])
    store.upsert_player_logs([PlayerGameLog(
        "G26-0001", "2026-27", "2026-10-21", "ZZZ-P00", "New Rookie", "AAA", {"pts": 10.0})])

    watch = injury_usage_spikes(store, fx.league_id, AS_OF)
    spike = next(s for s in watch.spikes if s.injured_player_id == victim)
    assert spike.injured_name == "AAA Player 0"  # not the bare "AAA-P00"


def test_stale_or_free_agent_out_designations_do_not_watch(fx):
    store, victim = fx.store, _victim(fx)
    stale = f"{TEAMS[1]}-P20"
    free_agent = f"{TEAMS[2]}-P20"
    _usage(store, stale, 30.0)
    _fresh_out(store, stale, known_from="2025-10-01")  # 41 days old: beyond the news window
    _usage(store, free_agent, 30.0)
    _fresh_out(store, free_agent)  # nobody rostered him: nothing to watch

    watch = injury_usage_spikes(store, fx.league_id, AS_OF)
    watched = {s.injured_player_id for s in watch.spikes}
    assert stale not in watched
    assert free_agent not in watched


def test_questionable_is_not_a_spike_yet(fx):
    store, victim = fx.store, _victim(fx)
    # the synthetic fixture already carries a QUESTIONABLE designation at AS_OF
    questionable = next(pid for pid, _n, _t in store.player_universe(fx.season)
                        if pid.endswith("-P03"))
    state = fx.store.league_state_asof(fx.league_id, AS_OF)
    if questionable not in state.rostered_player_ids():
        from fantasy_gm.data.simulate import _adp_order  # not rostered here: re-anchor
        questionable = _victim(fx)
        store.add_availability([
            Availability(questionable, "QUESTIONABLE", AS_OF, "official", 0.9, "test")])
    watch = injury_usage_spikes(store, fx.league_id, AS_OF)
    assert questionable not in {s.injured_player_id for s in watch.spikes}
