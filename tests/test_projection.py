"""Matchup projection: point-in-time, valid labels, variance-aware, injury-reactive."""

from __future__ import annotations

from fantasy_gm.data.store import Store
from fantasy_gm.engine.projection import Projector
from fantasy_gm.models import Availability, Game, Matchup, PlayerGameLog

SEASON = "2025-26"
PERIOD = ("2025-10-20", "2025-10-26")
AS_OF = "2025-10-20"


def _line(**cats):
    base = {k: 0.0 for k in ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fg_pct", "ft_pct")}
    base.update(cats)
    return base


def _tiny_store() -> Store:
    """Two teams, one player each. Same +2 projected margin in pts and stl, but stl has a
    much wider *measured* per-game spread than pts — so variance-awareness must come from the
    measured σ (no category multiplier)."""
    s = Store(":memory:")
    s.create_league("L", "L", SEASON, "weekly-lock",
                    ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fg_pct", "ft_pct"])
    s.add_team("L", "T0", "T0")
    s.add_team("L", "T1", "T1")
    s.add_roster_event("L", "T0", "P0", "add", "2025-10-01")
    s.add_roster_event("L", "T1", "P1", "add", "2025-10-01")
    s.add_matchup(Matchup("L", 0, PERIOD[0], PERIOD[1], "T0", "T1"))

    # pts is steady (mean 15, tiny spread); stl is swingy (mean 15, wide spread). Same means.
    s.upsert_games([Game("h1", SEASON, "2025-10-15", "X", "Y"),
                    Game("h2", SEASON, "2025-10-17", "X", "Y")])
    s.upsert_player_logs([
        PlayerGameLog("h1", SEASON, "2025-10-15", "P0", "P0", "X", _line(pts=14, stl=2)),
        PlayerGameLog("h2", SEASON, "2025-10-17", "P0", "P0", "X", _line(pts=16, stl=28)),
        PlayerGameLog("h1", SEASON, "2025-10-15", "P1", "P1", "Y", _line(pts=12, stl=0)),
        PlayerGameLog("h2", SEASON, "2025-10-17", "P1", "P1", "Y", _line(pts=14, stl=26)),
    ])
    # one remaining scheduled game each after as_of (schedule only)
    s.upsert_games([Game("fX", SEASON, "2025-10-21", "X", "Z"),
                    Game("fY", SEASON, "2025-10-21", "Y", "W")])
    return s


def test_high_variance_lead_reads_less_safe():
    proj = Projector().project(_tiny_store(), "L", "T0", AS_OF)
    # equal +2 margin in both, but stl's measured σ is far larger -> lower win prob than pts
    assert proj.categories["pts"].win_prob > proj.categories["stl"].win_prob


def test_labels_and_probabilities_valid():
    proj = Projector().project(_tiny_store(), "L", "T0", AS_OF)
    for p in proj.categories.values():
        assert 0.0 <= p.win_prob <= 1.0
        assert p.label in ("safe", "contested", "gone")


def test_injury_reopens_category():
    s = _tiny_store()
    before = Projector().project(s, "L", "T0", AS_OF).categories["pts"].win_prob
    s.add_availability([Availability("P0", "OUT", "2025-10-20", "official", 1.0, "")])
    after = Projector().project(s, "L", "T0", AS_OF).categories["pts"].win_prob
    assert after < before  # losing my only contributor moves pts toward gone


def test_projection_excludes_future_results():
    s = _tiny_store()
    before = Projector().project(s, "L", "T0", AS_OF).categories["pts"]
    # a result dated far in the future must not enter the as-of distribution
    s.upsert_player_logs([
        PlayerGameLog("future", SEASON, "2099-01-01", "P0", "P0", "X", _line(pts=999, stl=999))
    ])
    after = Projector().project(s, "L", "T0", AS_OF).categories["pts"]
    assert (before.mine_total, before.win_prob) == (after.mine_total, after.win_prob)
