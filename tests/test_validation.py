"""Validation harness: measured variance profile (A1), bootstrap win-prob (A3), A8 fix.

These run on synthetic data, so they check the *mechanism*, not the domain claim — the
synthetic season is generated from the very assumptions real data would validate.
"""

from __future__ import annotations

from fantasy_gm.data.store import Store
from fantasy_gm.models import Game, PlayerGameLog
from fantasy_gm.validation import (
    bootstrap_category_winprob,
    derive_variance_profile,
    measure_category_cv,
)

SEASON = "2025-26"


def _line(**c):
    base = {k: 0.0 for k in ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov",
                             "fgm", "fga", "ftm", "fta", "fg_pct", "ft_pct")}
    base.update(c)
    return base


# --- A1: measurement mechanism ----------------------------------------------

def _cv_store():
    """Players with a low-variance category (pts) and a high-variance one (stl)."""
    s = Store(":memory:")
    for pi in range(4):
        for gi, (pts, stl) in enumerate([(20, 0), (20, 4), (20, 0), (20, 4), (20, 0), (20, 4)]):
            gid = f"g{pi}-{gi}"
            s.upsert_games([Game(gid, SEASON, f"2025-10-{10 + gi:02d}", "X", "Y")])
            s.upsert_player_logs([
                PlayerGameLog(gid, SEASON, f"2025-10-{10 + gi:02d}", f"P{pi}", f"P{pi}", "X",
                              _line(pts=pts, stl=stl))
            ])
    return s


def test_cv_measured_and_high_variance_cat_ranks_higher():
    cv = measure_category_cv(_cv_store(), SEASON, min_games=3)
    assert "pts" in cv and "stl" in cv
    assert cv["stl"] > cv["pts"]  # constant pts (CV 0) vs swinging stl


def test_derive_profile_is_normalised_to_median():
    cv = {"pts": 0.2, "reb": 0.4, "stl": 0.8}
    prof = derive_variance_profile(cv)
    assert prof["reb"] == 1.0  # median category maps to 1.0
    assert prof["stl"] > prof["pts"]


# --- A3: bootstrap win-prob --------------------------------------------------

def test_bootstrap_returns_probability(fx):
    from fantasy_gm.validation import bootstrap_pct_winprob
    m = fx.store.matchup_for_team(fx.league_id, "T00", "2025-11-14")
    opp = m.team_b if m.team_a == "T00" else m.team_a
    mine = fx.store.roster_asof(fx.league_id, "T00", "2025-11-14")
    theirs = fx.store.roster_asof(fx.league_id, opp, "2025-11-14")
    p = bootstrap_category_winprob(fx.store, mine, theirs, "pts",
                                   m.period_start, "2025-11-14", m.period_end, n=300, window=10)
    assert 0.0 <= p <= 1.0
    # percentage-category bootstrap (A12) returns a valid probability too
    pp = bootstrap_pct_winprob(fx.store, mine, theirs, "fg_pct",
                               m.period_start, "2025-11-14", m.period_end, n=200, window=10)
    assert 0.0 <= pp <= 1.0


# --- A4: autocorrelation measurement -----------------------------------------

def test_category_correlations_matrix(fx):
    from fantasy_gm.validation import measure_category_correlations
    m = measure_category_correlations(fx.store, fx.season, pool_size=100)
    assert "pts" in m and "reb" in m["pts"]
    assert abs(m["pts"]["pts"] - 1.0) < 1e-6       # diagonal is 1
    assert abs(m["pts"]["reb"] - m["reb"]["pts"]) < 1e-6  # symmetric
    assert all(-1.0 <= v <= 1.0 for row in m.values() for v in row.values())


def test_autocorrelation_measured_for_counting_cats():
    from fantasy_gm.validation import measure_autocorrelation
    s = Store(":memory:")
    for gi in range(25):
        gid = f"g{gi}"
        s.upsert_games([Game(gid, SEASON, f"2025-11-{(gi % 27) + 1:02d}", "X", "Y")])
        s.upsert_player_logs([
            PlayerGameLog(gid, SEASON, f"2025-11-{(gi % 27) + 1:02d}", "P0", "P0", "X",
                          _line(pts=15 + (gi % 5), stl=(gi * 7) % 4))
        ])
    ac = measure_autocorrelation(s, SEASON, min_games=10)
    assert "pts" in ac and -1.0 <= ac["pts"] <= 1.0


# --- A8: percentage categories are volume-weighted ---------------------------

def test_percentage_category_is_volume_weighted():
    s = Store(":memory:")
    s.upsert_games([Game("g1", SEASON, "2025-10-16", "X", "Y"),
                    Game("g2", SEASON, "2025-10-18", "X", "Y")])
    # 3/10 one night, 7/10 the next -> volume-weighted FG% = 10/20 = 0.50, not (0.3+0.7)=1.0
    s.upsert_player_logs([
        PlayerGameLog("g1", SEASON, "2025-10-16", "P0", "P0", "X",
                      _line(fgm=3, fga=10, fg_pct=0.3)),
        PlayerGameLog("g2", SEASON, "2025-10-18", "P0", "P0", "X",
                      _line(fgm=7, fga=10, fg_pct=0.7)),
    ])
    totals = s.category_totals(["P0"], "2025-10-01", "2025-10-31", ["fg_pct"])
    assert abs(totals["fg_pct"] - 0.5) < 1e-9
