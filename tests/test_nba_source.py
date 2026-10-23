"""Unit-test the pure nba_api parse mapping (no network) so the field logic is verified
before the user runs the real backfill locally."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fantasy_gm.data.nba_source import (
    _parse_matchup,
    _to_minutes,
    parse_espn_scoreboard,
    parse_league_game_log,
    refresh_schedule,
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


# --- ESPN scoreboard schedule wire --------------------------------------------

ESPN_FIXTURE = Path(__file__).parent / "fixtures" / "espn_scoreboard_20261024.json"


def _espn_raw() -> dict:
    return json.loads(ESPN_FIXTURE.read_text())


def test_parse_espn_scoreboard_maps_teams_and_dates():
    games = parse_espn_scoreboard(_espn_raw(), "2026-27")
    # Live fixture: 8 events on the 2026-10-24 (US) slate, ids namespaced esp-*.
    assert len(games) == 8
    assert all(g.game_id.startswith("esp-") and g.season == "2026-27" for g in games)
    by_code = {(g.home_team, g.away_team) for g in games}
    # ESPN-only abbrevs must land on the store's NBA.com codes (SA->SAS etc. covered
    # by the identity entries in this fixture; WAS and SAS appear verbatim here).
    assert ("DAL", "SAS") in by_code and ("CHI", "WAS") in by_code
    # A late tip crossing UTC midnight stays on its US date.
    assert all(g.game_date == "2026-10-24" for g in games)


def test_parse_espn_scoreboard_skips_malformed_event():
    raw = {"events": [
        {"id": "1", "date": "2026-10-24T23:00Z",
         "competitions": [{"competitors": [
             {"homeAway": "home", "team": {"abbreviation": "BOS"}},
             {"homeAway": "away", "team": {"abbreviation": "NY"}},
         ]}]},
        {"id": "2", "date": "2026-10-24T23:00Z", "competitions": []},
        {"id": "3", "date": "2026-10-24T23:00Z",
         "competitions": [{"competitors": [
             {"homeAway": "home", "team": {"abbreviation": "LAL"}},
         ]}]},
    ]}
    games = parse_espn_scoreboard(raw, "2026-27")
    assert [(g.home_team, g.away_team) for g in games] == [("BOS", "NYK")]


def test_refresh_schedule_replaces_window_and_reports_change(tmp_path):
    from fantasy_gm.data.cache import RawCache
    from fantasy_gm.data.store import SCHEMA

    # Two-day window, cached payloads. Day 1 is the "today" slate and is scored, so it
    # stays cached (only unscored past payloads get refetched — no network in tests);
    # day 2 is strictly future and always cached.
    day1 = {"events": [{"id": "e1", "date": "2026-10-24T23:00Z",
                        "competitions": [{"competitors": [
                            {"homeAway": "home", "score": "110",
                             "team": {"abbreviation": "BOS"}},
                            {"homeAway": "away", "score": "99",
                             "team": {"abbreviation": "NY"}}]}]}]}
    day2 = {"events": [{"id": "e2", "date": "2026-10-25T23:00Z",
                        "competitions": [{"competitors": [
                            {"homeAway": "home", "team": {"abbreviation": "LAL"}},
                            {"homeAway": "away", "team": {"abbreviation": "GS"}}]}]}]}
    cache = RawCache(tmp_path / "cache")
    cache.set("espn.scoreboard", {"dates": "20261024"}, day1)
    cache.set("espn.scoreboard", {"dates": "20261025"}, day2)

    class _Stub:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self.conn.executescript(SCHEMA)
            self.prov: list[str] = []

        def upsert_games(self, games):
            self.conn.executemany(
                """INSERT INTO games(game_id, season, game_date, home_team, away_team,
                                     home_pts, away_pts)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(game_id) DO UPDATE SET
                     home_pts=excluded.home_pts, away_pts=excluded.away_pts""",
                [(g.game_id, g.season, g.game_date, g.home_team, g.away_team,
                  g.home_pts, g.away_pts) for g in games])
            self.conn.commit()

        def record_provenance(self, subject, note, recorded_at):
            self.prov.append(note)

    store = _Stub()
    in_window, changed, end = refresh_schedule(
        store, cache, "2026-27", "2026-10-24", window_days=2)
    assert in_window == 2 and changed and end == "2026-10-25"
    assert ("LAL", "GSW") in {(r["home_team"], r["away_team"]) for r in
                              store.conn.execute("SELECT * FROM games").fetchall()}

    # Identical re-run: nothing changed, no provenance churn.
    in_window, changed, _ = refresh_schedule(store, cache, "2026-27", "2026-10-24",
                                             window_days=2)
    assert in_window == 2 and not changed

    # A reschedule inside the window replaces the row (no duplicate) and reports change.
    day2["events"][0]["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "MIA"
    cache.set("espn.scoreboard", {"dates": "20261025"}, day2)
    in_window, changed, _ = refresh_schedule(store, cache, "2026-27", "2026-10-24",
                                             window_days=2)
    assert changed
    assert {r["home_team"] for r in store.conn.execute(
        "SELECT home_team FROM games WHERE game_id='esp-e2'").fetchall()} == {"MIA"}
    assert store.conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
