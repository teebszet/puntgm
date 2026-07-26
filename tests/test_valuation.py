"""Data-derived z-score valuation (A6)."""

from __future__ import annotations

from fantasy_gm.data.store import Store
from fantasy_gm.models import Game, PlayerGameLog
from fantasy_gm.valuation import player_values

SEASON = "2025-26"


def _line(**c):
    base = {k: 0.0 for k in ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov",
                             "fgm", "fga", "ftm", "fta", "fg_pct", "ft_pct")}
    base.update(c)
    return base


def _store_with(players: dict[str, dict]) -> Store:
    """players: {player_id: per-game line}; each plays 15 identical games."""
    s = Store(":memory:")
    for gi in range(15):
        gid = f"g{gi}"
        s.upsert_games([Game(gid, SEASON, f"2025-11-{gi + 1:02d}", "X", "Y")])
        s.upsert_player_logs([
            PlayerGameLog(gid, SEASON, f"2025-11-{gi + 1:02d}", pid, pid, "X", line)
            for pid, line in players.items()
        ])
    return s


def test_standout_ranks_highest_and_average_near_zero():
    players = {f"avg{i}": _line(pts=10, reb=5, ast=3) for i in range(10)}
    players["star"] = _line(pts=30, reb=12, ast=9)
    vals = player_values(_store_with(players), SEASON, pool_size=100)
    assert vals["star"] == max(vals.values())
    assert vals["star"] > 0
    # the identical average players all share one (negative, below-star) value
    avg_vals = {round(vals[p], 3) for p in players if p.startswith("avg")}
    assert len(avg_vals) == 1


def test_turnovers_count_negatively():
    players = {
        "clean": _line(pts=15, tov=1),
        "loose": _line(pts=15, tov=6),
        "mid": _line(pts=15, tov=3),
    }
    vals = player_values(_store_with(players), SEASON, pool_size=100)
    assert vals["clean"] > vals["mid"] > vals["loose"]  # fewer turnovers = more value


def test_percentage_impact_is_volume_weighted():
    # both shoot 90% FT (above the league avg set by the fillers), but one shoots 10/game and
    # one 1/game -> the high-volume 90% shooter has more category impact.
    players = {f"fill{i}": _line(pts=15, ftm=6, fta=8, ft_pct=0.75) for i in range(6)}
    players["volume"] = _line(pts=15, ftm=9, fta=10, ft_pct=0.9)
    players["sniper"] = _line(pts=15, ftm=0.9, fta=1, ft_pct=0.9)
    vals = player_values(_store_with(players), SEASON, pool_size=100)
    assert vals["volume"] > vals["sniper"]
