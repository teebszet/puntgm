"""Unit-test the pure nba_api parse mapping (no network) so the field logic is verified
before the user runs the real backfill locally."""

from __future__ import annotations

from fantasy_gm.data.nba_source import (
    _parse_matchup,
    _to_minutes,
    parse_league_game_log,
)

SEASON = "2024-25"


def _row(**kw):
    base = {
        "GAME_ID": "0022400001", "GAME_DATE": "2024-10-22", "TEAM_ABBREVIATION": "LAL",
        "TEAM_ID": 1610612747, "PLAYER_ID": 2544, "PLAYER_NAME": "LeBron James",
        "MATCHUP": "LAL vs. BOS", "MIN": 35, "PTS": 30, "REB": 8, "AST": 11, "STL": 2,
        "BLK": 1, "TOV": 4, "FG3M": 3, "FGM": 11, "FGA": 20, "FTM": 5, "FTA": 6,
        "FG_PCT": 0.55, "FT_PCT": 0.833,
    }
    base.update(kw)
    return base


def test_parse_maps_stats_and_types():
    games, logs, usage = parse_league_game_log([_row()], SEASON)
    assert len(logs) == 1
    lg = logs[0]
    assert lg.player_id == "2544" and lg.team == "LAL" and lg.game_date == "2024-10-22"
    assert lg.stats["pts"] == 30 and lg.stats["reb"] == 8 and lg.stats["fg3m"] == 3
    assert lg.stats["fg_pct"] == 0.55 and lg.stats["ftm"] == 5


def test_parse_derives_games_and_home_away():
    rows = [
        _row(PLAYER_ID=1, MATCHUP="LAL vs. BOS", TEAM_ABBREVIATION="LAL"),
        _row(PLAYER_ID=2, MATCHUP="BOS @ LAL", TEAM_ABBREVIATION="BOS"),
        _row(GAME_ID="0022400002", PLAYER_ID=3, MATCHUP="GSW @ DEN",
             TEAM_ABBREVIATION="GSW", GAME_DATE="2024-10-23"),
    ]
    games, logs, usage = parse_league_game_log(rows, SEASON)
    by_id = {g.game_id: g for g in games}
    assert by_id["0022400001"].home_team == "LAL" and by_id["0022400001"].away_team == "BOS"
    assert by_id["0022400002"].home_team == "DEN" and by_id["0022400002"].away_team == "GSW"
    assert len(logs) == 3


def test_usage_snapshot_from_minutes():
    _g, _l, usage = parse_league_game_log([_row(MIN=35), _row(PLAYER_ID=9, MIN=12)], SEASON)
    by_pid = {u.player_id: u for u in usage}
    assert by_pid["2544"].is_starter is True and by_pid["2544"].depth_chart_pos == 1
    assert by_pid["9"].is_starter is False and by_pid["9"].depth_chart_pos == 3


def test_minutes_and_matchup_helpers():
    assert _to_minutes(35) == 35.0
    assert abs(_to_minutes("34:30") - 34.5) < 1e-9
    assert _to_minutes(None) == 0.0
    assert _parse_matchup("LAL", "LAL vs. BOS") == ("LAL", "BOS")
    assert _parse_matchup("BOS", "BOS @ LAL") == ("LAL", "BOS")


def test_parse_feeds_the_store_and_validation():
    """End-to-end (offline): parsed rows go into the store and measure_category_cv runs."""
    from fantasy_gm.data.store import Store
    from fantasy_gm.validation import measure_category_cv
    rows = []
    for gi in range(6):
        rows.append(_row(GAME_ID=f"00224000{gi:02d}", GAME_DATE=f"2024-10-{22 + gi:02d}",
                         PTS=20 + gi, STL=gi % 3))
    games, logs, usage = parse_league_game_log(rows, SEASON)
    s = Store(":memory:")
    s.upsert_games(games)
    s.upsert_player_logs(logs)
    cv = measure_category_cv(s, SEASON, min_games=3)
    assert "pts" in cv and cv["pts"] >= 0.0
