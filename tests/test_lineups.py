"""Platform lineups: Yahoo payload parse, store reads, and lineup-aware projection.

The projector must price the platform's *scheduled lineup* — bench and IL accrue
nothing until slotted (Tim, 2026-10-23) — and fall back to the full roster when no
lineup has ever been read.
"""

from __future__ import annotations

import json
from pathlib import Path

from fantasy_gm.data.store import Store
from fantasy_gm.data.yahoo_fetch import parse_lineup_payload
from fantasy_gm.engine.projection import Projector
from fantasy_gm.models import Game, IncomingPlayer, Matchup, PlayerGameLog

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_lineups_20261023.json"
SEASON = "2025-26"
PERIOD = ("2025-10-20", "2025-10-26")


def _rows() -> list[tuple[str, str, str]]:
    return parse_lineup_payload(json.loads(FIXTURE.read_text()))


def test_parse_live_shape():
    rows = _rows()
    # 12 teams x (10 active slots + 3-4 bench/IL), from the live league 2026-10-23.
    assert len(rows) == 160
    assert len({t for t, _, _, _ in rows}) == 12
    tim = sorted(r for r in rows if r[0] == "478.l.25733.t.8")
    assert len(tim) == 14
    slots = {pid: slot for _, pid, slot, _n in tim}
    assert slots["10450"] == "IL" and slots["5327"] == "IL"   # Knueppel, Nurkić
    assert sum(1 for s in slots.values() if s not in ("BN", "IL")) == 10
    assert all(name for _, _, _, name in rows)  # the name rides along for the id join


def test_store_roundtrip_and_effective_dating():
    s = Store(":memory:")
    assert s.upsert_lineups("L", [("T0", "P0", "BN"), ("T0", "P1", "PG")], "2025-10-20") == 2
    # Re-slot P0 known later: an earlier as_of must never see it.
    assert s.upsert_lineups("L", [("T0", "P0", "Util")], "2025-10-23") == 1
    assert s.lineup_asof("L", "T0", "2025-10-21") == {"P0": "BN", "P1": "PG"}
    assert s.lineup_asof("L", "T0", "2025-10-23") == {"P0": "Util", "P1": "PG"}
    assert s.lineup_asof("L", "T1", "2025-10-23") == {}  # no read for that team
    # Idempotent: re-upserting the same read stores nothing new.
    assert s.upsert_lineups("L", [("T0", "P0", "BN"), ("T0", "P1", "PG")], "2025-10-20") == 0


def _store_with_lineup(lineup_known_from: str = "2025-10-01") -> Store:
    """One team, two players: P0 slotted (Util), P1 benched (BN). One shared NBA team
    with two remaining games, so the only difference is the lineup restriction."""
    s = Store(":memory:")
    s.create_league("L", "L", SEASON, "daily-change",
                    ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov", "fg_pct", "ft_pct"])
    s.add_team("L", "T0", "T0")
    s.add_team("L", "T1", "T1")
    for pid in ("P0", "P1"):
        s.add_roster_event("L", "T0", pid, "add", "2025-10-01")
    s.add_roster_event("L", "T1", "P2", "add", "2025-10-01")
    s.add_roster_event("L", "T1", "P3", "add", "2025-10-01")
    s.add_matchup(Matchup("L", 0, PERIOD[0], PERIOD[1], "T0", "T1"))
    s.upsert_games([Game("h1", SEASON, "2025-10-15", "X", "Y"),
                    Game("h2", SEASON, "2025-10-17", "X", "Y"),
                    Game("g1", "2026-27", "2025-10-23", "X", "Y"),
                    Game("g2", "2026-27", "2025-10-25", "Y", "X")])
    line = {"pts": 10.0, "reb": 5.0, "ast": 3.0, "stl": 1.0, "blk": 1.0, "fg3m": 1.0,
            "tov": 2.0, "fgm": 4.0, "fga": 9.0, "ftm": 2.0, "fta": 3.0}
    logs = [PlayerGameLog("h1", SEASON, "2025-10-15", pid, pid, "X", line)
            for pid in ("P0", "P1", "P2", "P3")]
    logs += [PlayerGameLog("h2", SEASON, "2025-10-17", pid, pid, "X", line)
             for pid in ("P0", "P1", "P2", "P3")]
    s.upsert_player_logs(logs)
    s.upsert_lineups("L", [("T0", "P0", "Util"), ("T0", "P1", "BN")], lineup_known_from)
    return s


def test_projection_benched_player_accrues_nothing():
    s = _store_with_lineup()
    p = Projector(participation=False)
    mine = p.team_projection(
        s, {}, ["P0", "P1"], "2025-10-20", PERIOD[0], PERIOD[1],
        p.config.categories, SEASON, active_ids=["P0"])
    full = p.team_projection(
        s, {}, ["P0", "P1"], "2025-10-20", PERIOD[0], PERIOD[1],
        p.config.categories, SEASON)
    # Two remaining games; the benched player doubles every remaining expectation.
    assert mine["pts"][0] * 2 == full["pts"][0]


def test_lineup_basis_ignores_stale_participation_rate():
    """The scheduled lineup is the current participation signal: a slotted player whose
    trailing rate is 0 (he sat last season's final games) must still project his games."""
    s = _store_with_lineup()
    # P0 missed one of his team's two logged games: his trailing rate is 0.5 — exactly
    # the stale-DNP shape an April rest sit leaves behind.
    s.conn.execute("DELETE FROM player_logs WHERE player_id='P0' AND game_date='2025-10-15'")
    s.conn.commit()
    p = Projector(participation=True)  # rate on: the live default
    lineup_based = p.team_projection(
        s, {}, ["P0", "P1"], "2025-10-20", PERIOD[0], PERIOD[1],
        p.config.categories, SEASON, active_ids=["P0"])
    roster_based = p.team_projection(
        s, {}, ["P0", "P1"], "2025-10-20", PERIOD[0], PERIOD[1],
        p.config.categories, SEASON)
    # Lineup basis: P0's full remaining schedule (2 games x 10 = 20), the stale rate
    # NOT applied. Roster basis (no lineup): rate applied, 0.5 x 2 x 10 + P1 20 = 30.
    assert abs(lineup_based["pts"][0] - 20.0) < 1e-6
    assert abs(roster_based["pts"][0] - 30.0) < 1e-6


def test_project_reads_lineup_from_store():
    s = _store_with_lineup()
    p = Projector(participation=False)
    proj = p.project(s, "L", "T0", "2025-10-20")
    # T0 prices only its slotted player: 1 player x 2 remaining games x 10 = 20 (the
    # tally window starts at period_start, so nothing is banked yet). The opponent has
    # no lineup read and falls back to its full roster: 2 players x 2 x 10 = 40.
    assert abs(proj.categories["pts"].mine_total - 20.0) < 1e-6
    assert abs(proj.categories["pts"].opp_total - 40.0) < 1e-6


def test_project_falls_back_without_lineup():
    s = _store_with_lineup(lineup_known_from="2099-01-01")  # dated in the future: invisible
    p = Projector(participation=False)
    proj = p.project(s, "L", "T0", "2025-10-20")
    # No lineup knowledge: full roster priced as before (2 players x 2 games x 10).
    assert abs(proj.categories["pts"].mine_total - 40.0) < 1e-6


def test_ingest_lineups_joins_to_nba_ids():
    from fantasy_gm.data.yahoo_import import ingest_lineups

    s = _store_with_lineup()  # P0/P1 on T0, P2/P3 on T1; logs carry the ids as names
    rows = [("T0", "Y1", "PG", "P0"), ("T0", "Y2", "BN", "P1"),
            ("T1", "Y3", "C", "P2"), ("T1", "Y9", "Util", "Not Anyone")]
    rep = ingest_lineups(s, "L", rows, "2025-10-20")
    assert rep["stored"] == 3 and rep["matched"] == 3
    assert rep["unmatched"] == [("Y9", "Not Anyone")]
    # Slot rows are keyed by the store's player ids.
    assert s.lineup_asof("L", "T0", "2025-10-20") == {"P0": "PG", "P1": "BN"}


def test_ingest_lineups_resolves_rookie_from_incoming_players():
    from fantasy_gm.data.yahoo_import import ingest_lineups

    s = _store_with_lineup()
    s.add_incoming_players(
        [IncomingPlayer("R1", "2026-27", "P Three", known_from="2025-06-01")])
    rep = ingest_lineups(s, "L", [("T1", "Y4", "SF", "P Three")], "2025-10-20")
    assert rep["stored"] == 1
    assert s.lineup_asof("L", "T1", "2025-10-20") == {"R1": "SF"}


def test_ingest_lineups_drafted_rookie_in_both_tables_is_not_ambiguous():
    """A 2026 draftee who has now logged games sits in player_logs AND
    incoming_players with the same NBA id. The name index must dedupe by id:
    duplicate rows are not ambiguity, and flagging one skipped his slot row so
    the projector priced him at zero remaining games (found live 2026-10-26 with
    six rookies)."""
    from fantasy_gm.data.yahoo_import import ingest_lineups

    s = _store_with_lineup()
    s.add_incoming_players(
        [IncomingPlayer("P2", "2026-27", "P Two", known_from="2025-06-01")])
    rep = ingest_lineups(s, "L", [("T1", "Y5", "PF", "P Two")], "2025-10-20")
    assert rep["ambiguous"] == []
    assert rep["matched"] == 1 and rep["stored"] == 1
    assert s.lineup_asof("L", "T1", "2025-10-20") == {"P2": "PF"}
