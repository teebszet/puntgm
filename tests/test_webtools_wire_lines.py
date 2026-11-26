"""Wire-lines board tests — the codified version of the hand-run table (8f81f55a).

The module must reproduce the hand-run's basis exactly: thresholds strictly more
than, rostered players never qualify, minutes from the same-date usage snapshot,
splits from makes/attempts, determinstic pts/reb/ast tie order, and games-left off
the live matchup week with an OUT player reading 0.
"""

from __future__ import annotations

import pytest

from fantasy_gm.data.simulate import simulate_league
from fantasy_gm.data.store import Store
from fantasy_gm.data.synthetic import seed_synthetic_season
from fantasy_gm.models import Availability, Game, PlayerGameLog, UsageRole
from fantasy_gm.webtools import wire_lines_payload

SEASON = "2025-26"
GDATE = "2025-11-11"  # inside the fixture week (Mon 11-09 .. Sun 11-15)
AS_OF = "2025-11-12"  # the morning after — default game_date reads GDATE

_FULL = {"fgm": 7, "fga": 14, "ftm": 0, "fta": 0, "fg3m": 1, "tov": 2}


def _wire_board() -> tuple[Store, str]:
    """Synthetic season + a small league (20 drafted), then hand-seeded wire lines."""
    store = Store(":memory:")
    seed_synthetic_season(store, season=SEASON, seed=7)
    league_id = simulate_league(store, season=SEASON, seed=1, n_teams=4, roster_size=5)

    store.upsert_games([
        Game("G-GAME", SEASON, GDATE, "ZZZ", "YYY", 110, 105),          # final
        Game("esp-G-GAME", SEASON, GDATE, "YYY", "ZZZ", None, None),    # esp twin, flipped
        Game("G-FUT1", SEASON, "2025-11-13", "XXX", "YYY", None, None),
        Game("G-FUT2", SEASON, "2025-11-14", "YYY", "XXX", None, None),
        Game("G-FUT3", SEASON, "2025-11-14", "WWW", "XXX", None, None),
    ])

    def log(pid: str, name: str, team: str, **stats) -> None:
        store.upsert_player_logs([PlayerGameLog(
            "G-GAME", SEASON, GDATE, pid, name, team, {**_FULL, **stats})])

    log("W-REB10", "Rebounder Ten", "YYY", pts=21, reb=10)          # beat pts, top tie
    log("W-REB4", "Rebounder Four", "YYY", pts=21, reb=4)           # same pts, reb tiebreak
    log("W-AT20", "Exactly Twenty", "ZZZ", pts=20, reb=9, ast=5, stl=2, blk=2)  # near miss
    log("W-OUT", "Out Remaining", "WWW", pts=12, reb=12)            # beats reb, but OUT
    store.add_usage_role([
        UsageRole("W-REB10", GDATE, 30.0, 14, True, 2),
        UsageRole("W-REB4", GDATE, 29.6, 14, True, 1),
        UsageRole("W-AT20", GDATE, 26.0, 14, True, 1),
    ])
    store.add_availability([Availability("W-OUT", "OUT", GDATE, "official", 1.0)])
    return store, league_id


def _rostered_one(store: Store, league_id: str) -> str:
    return next(iter(store.rosters_asof(league_id, AS_OF).values()))[0]


def test_thresholds_are_strict_and_rostered_excluded():
    store, league_id = _wire_board()
    drafted = _rostered_one(store, league_id)
    store.upsert_player_logs([PlayerGameLog(
        "G-GAME", SEASON, GDATE, drafted, "Drafted Stud", "ZZZ",
        {**_FULL, "pts": 40, "reb": 15})])

    payload = wire_lines_payload(store, league_id, AS_OF)
    ids = [r["player_id"] for r in payload["rows"]]

    assert ids == ["W-REB10", "W-REB4", "W-OUT"]  # pts desc, reb tiebreak, then reb cat
    assert payload["rows"][0]["beat"] == ["pts", "reb"]  # 21 pts AND 10 reb both clear
    assert payload["rows"][2]["beat"] == ["reb"]
    assert all(drafted != pid for pid in ids)
    assert all("W-AT20" != pid for pid in ids)  # exactly at every threshold: no beat
    assert payload["rostered_count"] == 20      # 4 teams x 5 — the full read, not a fold


def test_near_misses_are_exactly_at_threshold():
    store, _ = _wire_board()
    payload = wire_lines_payload(store, "no-such-league", AS_OF)
    assert payload["near_misses"] == [{
        "player_id": "W-AT20", "name": "Exactly Twenty", "nba_team": "ZZZ",
        "minutes": 26, "at": ["pts", "ast", "reb", "stl", "blk"],
        "stats": {"pts": 20.0, "reb": 9.0, "ast": 5.0, "stl": 2.0, "blk": 2.0,
                  "fg3m": 1.0, "tov": 2.0},
        "fg_pct": 50.0, "ft_pct": None, "games_left": None,
    }]


def test_minutes_splits_and_order():
    store, league_id = _wire_board()
    payload = wire_lines_payload(store, league_id, AS_OF)
    top, second = payload["rows"][0], payload["rows"][1]

    assert top["player_id"] == "W-REB10" and second["player_id"] == "W-REB4"
    assert top["minutes"] == 30 and second["minutes"] == 30  # 29.6 rounds to 30
    assert top["fg_pct"] == 50.0 and top["ft_pct"] is None   # 7/14; no FTA attempts
    assert top["stats"]["reb"] == 10.0 and second["stats"]["reb"] == 4.0


def test_games_left_reads_the_week_and_out_reads_zero():
    store, league_id = _wire_board()
    payload = wire_lines_payload(store, league_id, AS_OF)
    week = payload["week"]

    assert week is not None
    # Weeks anchor to the season's first Monday (2025-10-13), so this week is 11-10..16.
    assert week["start"] == "2025-11-10" and week["end"] == "2025-11-16"
    rows = {r["player_id"]: r for r in payload["rows"]}
    assert rows["W-REB10"]["games_left"] == 2   # YYY: unscored 11-13 + 11-14 (final excluded)
    assert rows["W-REB10"]["out"] is False
    assert rows["W-OUT"]["games_left"] == 0 and rows["W-OUT"]["out"] is True  # 2 games, OUT


def test_slate_dedupes_the_esptwin_and_counts_finals():
    store, _ = _wire_board()
    payload = wire_lines_payload(store, "no-such-league", AS_OF)
    slate = payload["slate"]

    # The synthetic season also plays on GDATE; assert OUR pair only: one entry for
    # {YYY, ZZZ} — the esp twin deduped away — in the official gid's orientation.
    mine = [m for m in slate["matchups"] if set(m.split("@")) == {"YYY", "ZZZ"}]
    assert mine == ["YYY@ZZZ"]
    assert slate["finals"] >= 1  # the official copy carries the result


def test_no_active_matchup_reads_null_week_and_games_left():
    store, _ = _wire_board()
    payload = wire_lines_payload(store, "no-such-league", AS_OF)
    assert payload["week"] is None
    assert all(r["games_left"] is None for r in payload["rows"])


def test_payload_is_deterministic():
    store, league_id = _wire_board()
    one = wire_lines_payload(store, league_id, AS_OF)
    store2, league_id2 = _wire_board()
    two = wire_lines_payload(store2, league_id2, AS_OF)
    assert one == two


def test_default_game_date_is_the_night_before():
    store, _ = _wire_board()
    payload = wire_lines_payload(store, "no-such-league", AS_OF)
    assert payload["game_date"] == GDATE
    assert payload["as_of"] == AS_OF


@pytest.mark.parametrize("cat,over,under", [
    ("pts", 21, 20), ("ast", 6, 5), ("reb", 10, 9),
    ("stl", 3, 2), ("blk", 3, 2),
])
def test_every_threshold_boundary_is_strict(cat, over, under):
    """One over the line qualifies, exactly at it does not — for all five cats."""
    for value, expect_row in ((over, True), (under, False)):
        store, _ = _wire_board()
        line = {c: 0 for c in ("pts", "reb", "ast", "stl", "blk")}
        line[cat] = value
        store.upsert_player_logs([PlayerGameLog(
            "G-GAME", SEASON, GDATE, "W-PROBE", "Probe", "YYY", {**_FULL, **line})])
        payload = wire_lines_payload(store, "no-such-league", AS_OF)
        ids = [r["player_id"] for r in payload["rows"]]
        assert ("W-PROBE" in ids) is expect_row
