"""Scoreboard matrix tests — every team against every team, with a week selector.

Tim's 2026-11-27 correction of the first scoreboard cut: the tab shows a full
round-robin matrix per week (not just the real matchups) across historical weeks
including the live one. Here we pin the shape: the week selector runs completed →
live, completed weeks prefer Yahoo's backfilled actuals and fall back to the store
tally (with the source stated), the live week reproduces the ``Projector`` numbers
the Matchup tab carries for every real matchup, and a bogus week is refused.
"""

from __future__ import annotations

import pytest

from fantasy_gm.config import PERCENTAGE_CATEGORIES, Config
from fantasy_gm.webtools import (
    scoreboard_matrix_payload,
    scoreboard_payload,
    team_names,
)

CATS = list(Config().categories)


def test_weeks_run_completed_to_live(fx):
    d = scoreboard_matrix_payload(fx.store, Config(), fx.league_id, fx.as_of)
    assert d["status"] == "live"
    assert d["week"] == max(w["week"] for w in d["weeks"])
    assert d["weeks"][-1]["status"] == "live"
    assert all(w["status"] == "completed" for w in d["weeks"][:-1])
    got = [w["week"] for w in d["weeks"]]
    assert got == list(range(got[0], got[-1] + 1)), "week selector must be contiguous"


def test_completed_week_prefers_yahoo_actuals(fx):
    period = fx.store.matchup_periods(fx.league_id)[0]
    names = team_names(fx.store, fx.league_id)
    seeded = {}
    for i, tid in enumerate(sorted(names)):
        stats = {"pts": 500.0 + i, "reb": 200.0 + i, "ast": 100.0 + i,
                 "stl": 50.0 + i / 2, "blk": 30.0 + i / 4, "fg3m": 60.0 + i,
                 "fg_pct": 0.450 + i / 1000, "ft_pct": 0.750 + i / 1000,
                 "tov": 80.0 + i / 2}
        seeded[tid] = stats
        fx.store.add_matchup_team_stats(fx.league_id, period["week"], tid, stats,
                                        "2025-11-01")
    d = scoreboard_matrix_payload(fx.store, Config(), fx.league_id, fx.as_of,
                                  week=period["week"])
    assert d["source"] == "yahoo"
    assert d["status"] == "completed"
    n = len(d["teams"])
    assert len(d["pairs"]) == n * (n - 1) // 2
    dirn = lambda c: -1 if c == "tov" else 1
    for p in d["pairs"]:
        a_stats, b_stats = seeded[p["a"]], seeded[p["b"]]
        aw = sum(1 for c in CATS if (a_stats[c] - b_stats[c]) * dirn(c) > 0)
        bw = sum(1 for c in CATS if (a_stats[c] - b_stats[c]) * dirn(c) < 0)
        ties = len(CATS) - aw - bw
        assert p["record"] == f"{aw}-{bw}" + (f"-{ties}" if ties else "")
        for r in p["cats"]:
            assert r["a"] == pytest.approx(a_stats[r["category"]], abs=0.06)
            assert r["b"] == pytest.approx(b_stats[r["category"]], abs=0.06)
            assert "win_prob" not in r, "completed weeks are deterministic"
        assert p["real"] is False


def test_completed_week_falls_back_to_banked_tally(fx):
    period = fx.store.matchup_periods(fx.league_id)[0]
    d = scoreboard_matrix_payload(fx.store, Config(), fx.league_id, fx.as_of,
                                  week=period["week"])
    assert d["source"] == "store"
    teams = [t["team_id"] for t in d["teams"]]
    a, b = teams[0], teams[1]
    rosters = fx.store.league_state_asof(fx.league_id, period["end"]).rosters
    pair = next(p for p in d["pairs"] if {p["a"], p["b"]} == {a, b})
    comp_keys = sorted({k for pair_ in PERCENTAGE_CATEGORIES.values() for k in pair_})
    for tid, side in ((a, "a"), (b, "b")):
        totals = fx.store.banked_totals_for_team(
            fx.league_id, tid, period["start"], period["end"], CATS + comp_keys,
            fallback_ids=rosters.get(tid, []))
        mine = {r["category"]: r[side] for r in pair["cats"]}
        for c in CATS:
            if c in PERCENTAGE_CATEGORIES:
                mk, at = PERCENTAGE_CATEGORIES[c]
                exp = (totals[mk] / totals[at]) if totals[at] else 0.0
            else:
                exp = float(totals[c])
            assert mine[c] == pytest.approx(exp, abs=0.011)


def test_live_week_matches_matchup_engine(fx):
    live = scoreboard_matrix_payload(fx.store, Config(), fx.league_id, fx.as_of)
    assert live["status"] == "live"
    assert live["source"] == "projected"
    n = len(live["teams"])
    assert len(live["pairs"]) == n * (n - 1) // 2
    assert all("games_remaining" in t for t in live["teams"])
    cards = scoreboard_payload(fx.store, Config(), fx.league_id, fx.as_of)
    for m in cards["matchups"]:
        pair = next(p for p in live["pairs"]
                    if {p["a"], p["b"]} == {m["team_a"]["team_id"],
                                            m["team_b"]["team_id"]})
        assert pair["real"] is True
        a_is_team_a = pair["a"] == m["team_a"]["team_id"]
        if a_is_team_a:
            assert pair["record"] == m["projected_score"]
        else:
            w, l, *rest = m["projected_score"].split("-")
            assert pair["record"] == f"{l}-{w}" + (f"-{rest[0]}" if rest else "")
        for r, cr in zip(pair["cats"], m["categories"]):
            mine_v, opp_v = (r["a"], r["b"]) if a_is_team_a else (r["b"], r["a"])
            assert mine_v == pytest.approx(cr["mine"], abs=0.011)
            assert opp_v == pytest.approx(cr["opp"], abs=0.011)
            wp = r["win_prob"] if a_is_team_a else round(1 - r["win_prob"], 2)
            assert wp == pytest.approx(cr["win_prob"], abs=0.02)


def test_completed_teams_carry_no_games_remaining(fx):
    period = fx.store.matchup_periods(fx.league_id)[0]
    d = scoreboard_matrix_payload(fx.store, Config(), fx.league_id, fx.as_of,
                                  week=period["week"])
    assert all("games_remaining" not in t for t in d["teams"])


def test_unknown_week_is_refused(fx):
    with pytest.raises(ValueError):
        scoreboard_matrix_payload(fx.store, Config(), fx.league_id, fx.as_of, week=99)
