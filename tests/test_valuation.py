"""Data-derived z-score valuation (A6)."""

from __future__ import annotations

from datetime import date, timedelta

from fantasy_gm.data.store import Store
from fantasy_gm.models import ForwardRoster, Game, PlayerGameLog, UsageRole
from fantasy_gm.valuation import player_values, rosterable_pool

SEASON = "2025-26"


def test_rosterable_pool_ranks_by_minutes_not_games():
    """A high-minutes star who missed games must outrank a low-minutes iron-man — the bug
    that stranded Jokić/Cade on the wire when the pool was ranked by games played."""
    s = Store(":memory:")
    base = date(2025, 11, 1)
    for gi in range(12):  # star: few games, big minutes
        d = (base + timedelta(days=gi * 2)).isoformat()
        s.upsert_games([Game(f"s{gi}", SEASON, d, "X", "Y")])
        s.upsert_player_logs(
            [PlayerGameLog(f"s{gi}", SEASON, d, "star", "Star", "X", _line(pts=25))])
        s.add_usage_role([UsageRole("star", d, 34.0, 18.0, True, 1)])
    for gi in range(40):  # iron-man: many games, bench minutes
        d = (base + timedelta(days=gi)).isoformat()
        s.upsert_games([Game(f"i{gi}", SEASON, d, "X", "Z")])
        s.upsert_player_logs(
            [PlayerGameLog(f"i{gi}", SEASON, d, "iron", "Iron", "Z", _line(pts=6))])
        s.add_usage_role([UsageRole("iron", d, 14.0, 5.0, False, 3)])
    assert rosterable_pool(s, SEASON, pool_size=1, min_games=10) == ["star"]


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


# --- R2: the pool reflects the current role, not a career average --------------


def test_pool_ranks_by_ranked_season_minutes_not_career_average():
    """A veteran whose role shrank ranks by THIS season's minutes (R2): the 36-minute role
    he had last season must not outrank the 20-minute role he holds now."""
    s = Store(":memory:")
    for gi in range(30):  # last season: 36 minutes a night
        d = (date(2024, 11, 1) + timedelta(days=gi)).isoformat()
        s.upsert_games([Game(f"o{gi}", "2024-25", d, "X", "Y")])
        s.upsert_player_logs(
            [PlayerGameLog(f"o{gi}", "2024-25", d, "vet", "Vet", "X", _line(pts=25))])
        s.add_usage_role([UsageRole("vet", d, 36.0, 18.0, True, 1)])
    for gi in range(12):  # this season: the role that must count — 20 minutes
        d = (date(2025, 11, 1) + timedelta(days=gi)).isoformat()
        s.upsert_games([Game(f"v{gi}", SEASON, d, "X", "Y")])
        s.upsert_player_logs(
            [PlayerGameLog(f"v{gi}", SEASON, d, "vet", "Vet", "X", _line(pts=12))])
        s.add_usage_role([UsageRole("vet", d, 20.0, 8.0, False, 3)])
    for gi in range(12):  # the player who took the role: 24 minutes
        d = (date(2025, 11, 1) + timedelta(days=gi)).isoformat()
        s.upsert_games([Game(f"r{gi}", SEASON, d, "X", "Z")])
        s.upsert_player_logs(
            [PlayerGameLog(f"r{gi}", SEASON, d, "riser", "Riser", "Y", _line(pts=18))])
        s.add_usage_role([UsageRole("riser", d, 24.0, 12.0, True, 2)])
    pool = rosterable_pool(s, SEASON, pool_size=2, min_games=10)
    assert pool[0] == "riser"  # career-average minutes (31.4) would have ranked vet first


def test_no_history_player_is_placed_by_forward_roster_depth():
    """A rookie with no NBA logs must not vanish from the pool he will be drafted from (R2);
    his pool position derives from depth on the projected roster."""
    s = Store(":memory:")
    for gi in range(12):
        d = (date(2025, 11, 1) + timedelta(days=gi)).isoformat()
        s.upsert_games([Game(f"s{gi}", SEASON, d, "X", "Y")])
        s.upsert_player_logs(
            [PlayerGameLog(f"s{gi}", SEASON, d, "star", "Star", "X", _line(pts=25))])
        s.add_usage_role([UsageRole("star", d, 34.0, 18.0, True, 1)])
    s.add_forward_roster([
        ForwardRoster("rookie", "2026-27", "X", 1, known_from="2026-08-17", role="rookie"),
    ])
    assert "rookie" not in rosterable_pool(s, SEASON, pool_size=8)
    pool = rosterable_pool(s, SEASON, pool_size=8, forward_season="2026-27")
    assert pool.index("rookie") < pool.index("star")  # depth 1 implies starter minutes


def test_season_lost_to_injury_places_by_depth_not_zero():
    """A player whose ranked season has no usable sample — a season lost to injury — is
    placed by depth on his projected roster, not zero-ranked out of the pool (R2)."""
    s = Store(":memory:")
    for gi in range(12):
        d = (date(2025, 11, 1) + timedelta(days=gi)).isoformat()
        s.upsert_games([Game(f"v{gi}", SEASON, d, "X", "Y")])
        s.upsert_player_logs(
            [PlayerGameLog(f"v{gi}", SEASON, d, "vet", "Vet", "X", _line(pts=15))])
        s.add_usage_role([UsageRole("vet", d, 30.0, 14.0, True, 1)])
    for gi in range(6):  # the injured star: DNP rows only — no usable sample
        d = (date(2025, 11, 20) + timedelta(days=gi)).isoformat()
        s.upsert_games([Game(f"h{gi}", SEASON, d, "X", "Y")])
        s.upsert_player_logs(
            [PlayerGameLog(f"h{gi}", SEASON, d, "halib", "Halib", "X", _line())])
        s.add_usage_role([UsageRole("halib", d, 0.0, 0.0, False, 5)])
    s.add_forward_roster([
        ForwardRoster("halib", "2026-27", "IND", 2, known_from="2026-08-17", role="returning"),
    ])
    pool = rosterable_pool(s, SEASON, pool_size=8, forward_season="2026-27")
    assert pool.index("halib") < pool.index("vet")  # depth 2 (34 implied) above 30-minute vet
