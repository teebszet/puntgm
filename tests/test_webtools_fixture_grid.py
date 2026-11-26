"""Fixture-grid tests — team defense from stored box scores (T1).

Ground truth: the synthetic season's logs sum to its game scores by construction, so
allowed points derived from logs must equal the mean opponent score read straight off
``games`` — an independent check that touches only the schedule table, never the logs
the module aggregates. Rates must aggregate volume-weighted (config A8), favorability
must be direction-aware (a turnover-forcing defense is BAD for the attacker), and thin
season samples must fall back to the previous season rather than masquerade.
"""

from __future__ import annotations



import pytest

from fantasy_gm.data.store import Store
FX_SEASON = "2025-26"  # conftest.SEASON: the synthetic fixture season
from fantasy_gm.models import Availability, Game, PlayerGameLog, PlayerPosition
from fantasy_gm.webtools import fixture_grid_payload, team_defense

MID = "2025-11-11"  # the conftest fixture's mid-season date

_FULL_LINE = {"pts": 20, "reb": 8, "ast": 4, "stl": 1, "blk": 1, "fg3m": 2,
              "fgm": 8, "fga": 16, "ftm": 4, "fta": 5, "tov": 2}


def _hand_store(cur_games: int, prev_games: int, lines: tuple[dict, dict]) -> Store:
    """AAA vs BBB with only BBB players logged (AAA is the only defense with data).

    ``lines`` are the per-game box lines of BBB's two players. Game rows carry no
    scores — the defense model reads logs; scores only matter for the synthetic
    ground-truth test.
    """
    store = Store(":memory:")
    games: list[Game] = []
    logs: list[PlayerGameLog] = []
    seq = 0

    def add(season: str, n: int, start_month: int) -> None:
        nonlocal seq
        for i in range(n):
            seq += 1
            gdate = f"{season[:4]}-{start_month:02d}-{(i % 27) + 1:02d}"
            home, away = ("AAA", "BBB") if i % 2 == 0 else ("BBB", "AAA")
            games.append(Game(f"H-{season}-{seq}", season, gdate, home, away, 0, 0))
            for j, line in enumerate(lines):
                logs.append(PlayerGameLog(
                    f"H-{season}-{seq}", season, gdate, f"BBB-P{j}",
                    f"BBB Player {j}", "BBB", dict(line)))

    add("2024-25", prev_games, 11)
    add(FX_SEASON, cur_games, 11)
    store.upsert_games(games)
    store.upsert_player_logs(logs)
    return store


def test_allowed_points_matches_schedule_ground_truth(fx):
    """Allowed points off the logs == mean opponent score off the games table."""
    defense = team_defense(fx.store, MID)
    assert len(defense.allowed) == 8
    for team, rates in defense.allowed.items():
        scores = [
            (r["away_pts"] if r["home_team"] == team else r["home_pts"])
            for r in fx.store.conn.execute(
                """SELECT home_team, away_team, home_pts, away_pts FROM games
                   WHERE game_date <= ? AND (home_team = ? OR away_team = ?)""",
                (MID, team, team))
        ]
        assert scores, f"{team} played before {MID}"
        assert rates["pts"] == pytest.approx(sum(scores) / len(scores), abs=1e-9)


def test_rate_cats_volume_weighted():
    """fg% allowed is Σmakes/Σattempts against the defense, never a rate average (A8)."""
    # shooter A: 2/2 every game; shooter B: 0/4 every game.
    a = {**_FULL_LINE, "fgm": 2, "fga": 2}
    b = {**_FULL_LINE, "fgm": 0, "fga": 4}
    store = _hand_store(cur_games=6, prev_games=2, lines=(a, b))
    defense = team_defense(store, "2025-11-11")
    assert defense.team_basis["AAA"] == FX_SEASON
    assert defense.allowed["AAA"]["fg_pct"] == pytest.approx(2 / 6, abs=1e-9)
    assert defense.allowed["AAA"]["fg_pct"] != pytest.approx(0.5, abs=1e-6)  # mean-of-rates
    assert defense.allowed["AAA"]["pts"] == pytest.approx(_FULL_LINE["pts"] * 2)


def test_thin_current_season_falls_back_to_previous():
    """A team under the current-season minimum prices from the previous season."""
    line = dict(_FULL_LINE)
    store = _hand_store(cur_games=2, prev_games=6, lines=(line, line))
    defense = team_defense(store, "2025-11-11")  # default min_team_games = 5
    assert defense.team_basis["AAA"] == "2024-25"
    assert defense.team_games["AAA"] == 6
    assert defense.allowed["AAA"]["pts"] == pytest.approx(_FULL_LINE["pts"] * 2)


def test_favor_is_direction_aware(fx):
    """Positive cats favor the most-allowing defense; turnovers invert."""
    defense = team_defense(fx.store, MID)
    by_pts = sorted(defense.allowed, key=lambda t: -defense.allowed[t]["pts"])
    easy, hard = by_pts[0], by_pts[-1]
    assert defense.favor[easy]["pts"] > 1.0 > defense.favor[hard]["pts"]

    by_tov = sorted(defense.allowed, key=lambda t: -defense.allowed[t]["tov"])
    most_forcing, least_forcing = by_tov[0], by_tov[-1]
    # the defense that forces the most turnovers is the WORST matchup for tov
    assert defense.favor[most_forcing]["tov"] < defense.favor[least_forcing]["tov"]

    for team in defense.allowed:
        for cat, f in defense.favor[team].items():
            assert f > 0
            assert 1 <= defense.rank[team][cat] <= len(defense.allowed)


def _all_player_ids(fx) -> list[str]:
    return sorted(r["player_id"] for r in
                  fx.store.conn.execute("SELECT DISTINCT player_id FROM player_logs"))


def test_position_split_matches_team_rates_when_universal(fx):
    """Every player marked 'C' ⇒ each (defense,'C') cell is the per-player-per-game
    average of ALL production facing that defense (rows divisor), and the position
    favor equals the team favor (the fixture's rows-per-game is uniform across
    teams, so the per-player average is proportional to the per-game total)."""
    fx.store.add_player_positions([
        PlayerPosition(pid, "C", "2025-10-01", "nba") for pid in _all_player_ids(fx)])
    defense = team_defense(fx.store, MID)
    assert len(defense.by_position) == len(defense.allowed)
    for team in defense.allowed:
        rows, pts = fx.store.conn.execute(
            """SELECT COUNT(*), SUM(json_extract(stats_json, '$.pts'))
               FROM player_logs l JOIN games g ON g.game_id = l.game_id
               WHERE l.game_date <= ? AND l.season = '2025-26'
                 AND ((l.team = g.home_team AND g.away_team = ?)
                   OR (l.team = g.away_team AND g.home_team = ?))""",
            (MID, team, team)).fetchone()
        cell = defense.by_position[(team, "C")]
        assert defense.position_games[(team, "C")] == rows
        assert cell["pts"] == pytest.approx(pts / rows, abs=1e-9)
        assert defense.position_favor[(team, "C")] == pytest.approx(
            defense.favor[team], abs=1e-9)
        assert defense.position_rank[(team, "C")] == defense.rank[team]


def test_thin_position_cell_falls_back_to_team_rate(fx):
    """One positioned player per team is ~15 player-games — under the minimum, so no
    (defense, 'G') split may exist anywhere."""
    firsts: dict[str, list[str]] = {}
    for pid in _all_player_ids(fx):
        firsts.setdefault(pid.split("-")[0], []).append(pid)
    fx.store.add_player_positions([
        PlayerPosition(ids[0], "G", "2025-10-01", "nba") for ids in firsts.values()])
    defense = team_defense(fx.store, MID)
    assert not any(pos == "G" for _team, pos in defense.by_position)


def _fx_lineup(fx, tid: str) -> list[tuple[str, str, str]]:
    roster = fx.store.roster_asof(fx.league_id, tid, fx.as_of)
    slots = ["PG", "SG", "G", "SF", "PF", "F", "C", "Util", "Util", "Util",
             "BN", "BN", "BN"]
    return [(tid, pid, slots[i % len(slots)]) for i, pid in enumerate(roster)]


def _away_side(schedule: dict, date: str, team: str) -> str:
    """The away side of ``team``'s game on ``date`` (schedule orientation ground truth)."""
    for home, away in schedule.get(date, ()):
        if team in (home, away):
            return away
    raise AssertionError(f"{team} has no game on {date}")


def test_grid_payload_rows_match_schedule(fx):
    fx.store.upsert_lineups(fx.league_id, _fx_lineup(fx, "T00"), fx.as_of)
    payload = fixture_grid_payload(fx.store, fx.league_id, "T00", fx.as_of, days=14)
    assert payload["as_of"] == fx.as_of
    assert len(payload["dates"]) == 14 and payload["dates"][0] == fx.as_of
    assert payload["players"], "lineup seeded, so rows exist"
    assert {p["slot"] for p in payload["players"]} <= {
        s for _t, _p, s in _fx_lineup(fx, "T00")}

    schedule: dict[str, set[tuple[str, str]]] = {}
    for r in fx.store.conn.execute(
            "SELECT game_date, home_team, away_team FROM games "
            "WHERE game_date >= ? AND game_date <= ?",
            (fx.as_of, payload["dates"][-1])):
        schedule.setdefault(r["game_date"], set()).add((r["home_team"], r["away_team"]))

    for p in payload["players"]:
        for g in p["games"]:
            pair = {(p["nba_team"], g["opponent"]), (g["opponent"], p["nba_team"])}
            assert pair & schedule.get(g["date"], set()), \
                f"{p['name']} {g['date']} vs {g['opponent']} not on the schedule"
            assert g["is_home"] == (g["opponent"] == _away_side(schedule, g["date"],
                                                               p["nba_team"]))
            for cell in g["favorability"].values():
                assert 0 < cell["favor"]
                assert cell["rank"] is None or 1 <= cell["rank"] <= 8
                assert cell["basis"] in ("team", "position")


def test_grid_payload_skips_tbd_and_surfaces_status(fx):
    fx.store.upsert_lineups(fx.league_id, _fx_lineup(fx, "T00"), fx.as_of)
    target = fx.store.roster_asof(fx.league_id, "T00", fx.as_of)[0]
    nba = fx.store.player_team(target, fx.as_of)
    row = fx.store.conn.execute(
        "SELECT game_date, home_team, away_team FROM games WHERE game_date >= ? "
        "AND (home_team = ? OR away_team = ?) LIMIT 1",
        (fx.as_of, nba, nba)).fetchone()
    assert row is not None, "fixture team plays within the window"
    fx.store.conn.execute(        "UPDATE games SET away_team = 'TBD' WHERE game_date = ? AND home_team = ? "
        "AND away_team = ?",
        (row["game_date"], row["home_team"], row["away_team"]))
    fx.store.add_availability([Availability(target, "OUT", fx.as_of, "official", 0.95)])

    payload = fixture_grid_payload(fx.store, fx.league_id, "T00", fx.as_of, days=14)
    by_id = {p["player_id"]: p for p in payload["players"]}
    assert by_id[target]["status"] == "OUT", "availability_asof surfaces on the row"
    dates = [g["date"] for g in by_id[target]["games"]]
    assert row["game_date"] not in dates, "TBD opponent must not render as a cell"
    cells = [g for g in by_id[target]["games"]]
    if cells:
        assert cells[0]["favorability"], "real opponent carries favorability"


def test_payload_uses_position_basis_for_universal_positions(fx):
    """All players positioned ⇒ every grid cell is position-based and numerically
    equals the team-level favor for that opponent."""
    fx.store.upsert_lineups(fx.league_id, _fx_lineup(fx, "T00"), fx.as_of)
    fx.store.add_player_positions([
        PlayerPosition(pid, "C", "2025-10-01", "nba") for pid in _all_player_ids(fx)])
    defense = team_defense(fx.store, fx.as_of)
    payload = fixture_grid_payload(fx.store, fx.league_id, "T00", fx.as_of, days=14)
    marked = [g for p in payload["players"] for g in p["games"]]
    assert marked, "T00 faces opponents within the window"
    for g in marked:
        opp = g["opponent"]
        for cat, cell in g["favorability"].items():
            assert cell["basis"] == "position"
            assert cell["favor"] == round(defense.favor[opp][cat], 2)
            assert cell["rank"] == defense.rank[opp][cat]


def test_schedule_dedupes_flipped_duplicate_game(fx):
    """The same real game stored twice (ESPN + NBA gid) with conflicting home/away
    renders ONE cell, with the official gid's orientation."""
    from fantasy_gm.webtools.fixture_grid import _schedule
    fx.store.conn.execute(
        "INSERT INTO games (game_id, season, game_date, home_team, away_team, "
        "home_pts, away_pts) VALUES ('esp-999', '2025-26', '2025-12-01', 'BBB', 'AAA', "
        "NULL, NULL)")
    fx.store.conn.execute(
        "INSERT INTO games (game_id, season, game_date, home_team, away_team, "
        "home_pts, away_pts) VALUES ('0012600001', '2025-26', '2025-12-01', 'AAA', 'BBB', "
        "NULL, NULL)")
    sched = _schedule(fx.store, "2025-12-01", "2025-12-01")
    assert sched["2025-12-01"] == [{"home": "AAA", "away": "BBB"}]


def test_covering_read_beats_fold_for_traded_away_players(fx):
    """The payload's rows come from the covering dated read — a player dropped
    between reads must not resurrect (live 2026-10-27 class)."""
    fx.store.upsert_lineups(fx.league_id, _fx_lineup(fx, "T00"), "2025-11-01")
    original = fx.store.roster_asof(fx.league_id, "T00", fx.as_of)
    dropped, added = original[0], "ZZZ-P99"
    trimmed = [(tid, pid, slot) for tid, pid, slot in _fx_lineup(fx, "T00")
               if pid != dropped] + [("T00", added, "Util")]
    fx.store.upsert_lineups(fx.league_id, trimmed, fx.as_of)
    assert fx.store.conn.execute(
        "SELECT COUNT(*) FROM player_logs WHERE player_id = 'ZZZ-P99'").fetchone()[0] == 0

    payload = fixture_grid_payload(fx.store, fx.league_id, "T00", fx.as_of, days=14)
    ids = {p["player_id"] for p in payload["players"]}
    assert dropped not in ids, "the fold would resurrect the dropped player"
    assert added in ids, "the covering read's new player is present"


def test_empty_store_is_loud():
    store = Store(":memory:")
    with pytest.raises(ValueError):
        team_defense(store, MID)
