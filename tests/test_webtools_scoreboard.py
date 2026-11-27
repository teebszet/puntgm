"""Scoreboard tests — every active matchup priced once, both sides per call.

The numbers themselves are ``Projector`` outputs, proven in the projection tests;
here we pin the league-wide shape: teams pair exactly once, each matchup carries
the full category set with bands, the projected score accounts for every
category, and both sides carry the settled games-remaining number.
"""

from __future__ import annotations

from fantasy_gm.config import Config
from fantasy_gm.webtools import scoreboard_payload


def test_pairs_every_team_exactly_once(fx):
    payload = scoreboard_payload(fx.store, Config(), fx.league_id, fx.as_of)
    teams = [t for m in payload["matchups"]
             for t in (m["team_a"]["team_id"], m["team_b"]["team_id"])]
    assert len(teams) == len(set(teams)), "a team priced twice would double-render"
    assert len(payload["matchups"]) == 4  # 8-team fixture league, all mid-week


def test_rows_carry_bands_and_score_counts_all_cats(fx):
    payload = scoreboard_payload(fx.store, Config(), fx.league_id, fx.as_of)
    cats = Config().categories
    for m in payload["matchups"]:
        assert m["period_start"] <= fx.as_of <= m["period_end"]
        assert [r["category"] for r in m["categories"]] == list(cats)
        for r in m["categories"]:
            assert 0.0 <= r["win_prob"] <= 1.0
            assert r["label"] in ("safe", "contested", "gone")
            assert r["mine_lo"] <= r["mine"] <= r["mine_hi"]
            assert r["opp_lo"] <= r["opp"] <= r["opp_hi"]
        parts = [int(x) for x in m["projected_score"].split("-")]
        assert sum(parts) == len(cats)
        assert m["team_a"]["name"] and m["team_b"]["name"]


def test_games_remaining_is_yahoo_comparable(fx):
    payload = scoreboard_payload(fx.store, Config(), fx.league_id, fx.as_of)
    for m in payload["matchups"]:
        for side in ("team_a", "team_b"):
            gr = m[side]["games_remaining"]
            assert gr is None or (isinstance(gr, int) and gr >= 0)
