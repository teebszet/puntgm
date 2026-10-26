"""Daily GM run digest: Yahoo-style games-remaining counts.

The 2026-10-26 bug this pins down: the digest (and my ad-hoc board read) priced
games remaining over the *scheduled starters only* — 30 vs 33 — while Yahoo's
matchup counter sums every rostered player not on IL, bench included — 40. The
number the digest shows must be the number Yahoo shows, or it reads as a bug.
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
    """Duck-typed Store: two teams, a bench player, an IL player."""

    PERIOD = ("2026-10-26", "2026-11-01")
    # pid -> (team, games remaining in the window)
    GAMES = {"s1": ("OKC", 3), "s2": ("BOS", 4), "b1": ("LAL", 4), "i1": ("CHA", 4)}

    def __init__(self):
        self.matchup = _Matchup("t.8", "t.12", *self.PERIOD)

    def matchup_for_team(self, _league, team_id, _as_of):
        return self.matchup

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


def test_games_remaining_counts_bench_excludes_il():
    mod = _load()
    mine, opp = mod.games_remaining(_FakeStore(), "yahoo-x", "t.8", "2026-10-26")
    # mine: starters 3+4, bench 4 (Luka-style BN), IL 4 excluded from the Yahoo total
    assert mine == (7, 4, 4)
    # opponent: no IL -> everything he has counts, bench included
    assert opp == (7, 4, 0)


def test_games_remaining_unslotted_prices_like_bench():
    mod = _load()
    store = _FakeStore()
    store.lineup_asof = lambda *a, **k: {}  # no lineup knowledge at all
    mine, _opp = mod.games_remaining(store, "yahoo-x", "t.8", "2026-10-26")
    # every rostered player counts (Yahoo's counter doesn't need slots): with no
    # slots everything reads bench, and the Yahoo-comparable total is unchanged
    assert mine == (0, 15, 0) and mine[0] + mine[1] == (7 + 4) + 4


def test_render_games_block_names_teams_and_splits():
    mod = _load()
    lines = mod.render_games_block(((7, 4, 4), (7, 4, 0)), "Mine", "Theirs")
    text = "\n".join(lines)
    assert "Mine: **11** (7 starters + 4 bench; 4 on IL don't count)" in text
    assert "Theirs: **11** (7 starters + 4 bench)" in text
    assert mod.render_games_block(None, "Mine", "Theirs") == []
