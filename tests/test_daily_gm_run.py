"""Daily GM run digest: Yahoo-style games-remaining counts.

The 2026-10-26/27 chase this pins down: Yahoo's matchup counter counts, per
remaining date, the players in that date's *scheduled starting* slots whose NBA
team plays that date — bench and IL never count. Verified live: the per-day sum
over Yahoo's own scheduled-lineup reads (`roster;date=…`) reproduced Yahoo's
40/41 exactly, where the current-lineup-for-the-week model gave 30/33 and a
bench-inclusive model gave 40/42 (a coincidence on my side). A manager who
re-slots daily to maximise games must be priced on exactly the days each player
is scheduled to be slotted.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "daily_gm_run.py"


def _load():
    spec = importlib.util.spec_from_file_location("daily_gm_run", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Matchup:
    def __init__(self, a: str, b: str, start: str, end: str):
        self.team_a, self.team_b = a, b
        self.period_start, self.period_end = start, end


class _FakeStore:
    """Duck-typed Store: two teams, a bench player, an IL player.

    `scheduled_active_games` returns None by default so the tests exercise the
    current-lineup fallback; the per-day test overrides it.
    """

    PERIOD = ("2026-10-26", "2026-11-01")
    # pid -> (team, games remaining in the window)
    GAMES = {"s1": ("OKC", 3), "s2": ("BOS", 4), "b1": ("LAL", 4), "i1": ("CHA", 4)}

    def __init__(self):
        self.matchup = _Matchup("t.8", "t.12", *self.PERIOD)
        self.per_day = None

    def matchup_for_team(self, _league, team_id, _as_of):
        return self.matchup

    def scheduled_active_games(self, _league, team_id, _as_of, _end):
        return (self.per_day or {}).get(team_id)

    def rosters_asof(self, _league, _as_of):
        return {
            "t.8": ["s1", "s2", "b1", "i1"],
            "t.12": ["s1", "s2", "b1"],
        }

    def lineup_asof(self, _league, team_id, _as_of):
        return {
            "t.8": {"s1": "PG", "s2": "C", "b1": "BN", "i1": "IL"},
            "t.12": {"s1": "PG", "s2": "Util", "b1": "BN"},
        }[team_id]

    def player_team(self, pid, _as_of):
        return self.GAMES.get(pid, (None, 0))[0]

    def remaining_games_for_team(self, team, _after, _end):
        return sum(g for _t, g in self.GAMES.values() if _t == team)


def test_games_remaining_prices_per_day_scheduled_lineups():
    mod = _load()
    store = _FakeStore()
    # Per-date scheduled lineups: the manager re-slots daily, so s1 has 3 games
    # slotted and s2 has 2 (his other games go to bench players that date).
    store.per_day = {"t.8": {"s1": 3, "s2": 2}, "t.12": {"s1": 3, "s2": 4}}
    mine, opp, per_day = mod.games_remaining(store, "yahoo-x", "t.8", "2026-10-26")
    assert (mine, opp, per_day) == (5, 7, True)


def test_games_remaining_fallback_counts_starters_only():
    mod = _load()
    # No per-date lineup coverage -> current-lineup starters over the whole
    # window; bench (4) and IL (4) never count, and per_day says so.
    mine, opp, per_day = mod.games_remaining(_FakeStore(), "yahoo-x", "t.8", "2026-10-26")
    assert (mine, opp, per_day) == (7, 7, False)


def test_games_remaining_no_active_matchup_is_none():
    mod = _load()
    store = _FakeStore()
    store.matchup_for_team = lambda *a, **k: None
    assert mod.games_remaining(store, "yahoo-x", "t.8", "2026-10-26") is None


def test_render_games_block_states_basis():
    mod = _load()
    per_day = "\n".join(mod.render_games_block((40, 41, True), "Mine", "Theirs"))
    assert "per-day scheduled starters" in per_day
    assert "Mine: **40**" in per_day and "Theirs: **41**" in per_day
    fallback = "\n".join(mod.render_games_block((40, 41, False), "Mine", "Theirs"))
    assert "per-date lineup reads missing" in fallback
    assert mod.render_games_block(None, "Mine", "Theirs") == []
