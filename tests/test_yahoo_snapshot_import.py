"""End-to-end: a Yahoo snapshot -> crosswalk -> reconstruction -> point-in-time store."""

from __future__ import annotations

import pytest

from fantasy_gm.data.store import Store
from fantasy_gm.data.yahoo_fetch import normalize_transactions
from fantasy_gm.data.yahoo_import import ImportRefused, import_snapshot
from fantasy_gm.models import PlayerGameLog

SEASON = "2025-26"
NBA = {"201939": "Stephen Curry", "203999": "Nikola Jokić", "1626157": "Karl-Anthony Towns",
       "1627759": "Jaylen Brown", "202681": "Kyrie Irving", "1628369": "Jayson Tatum"}
# Yahoo ids -> Yahoo-style names (accents stripped, punctuation dropped)
YAHOO = {"y1": "Stephen Curry", "y2": "Nikola Jokic", "y3": "Karl Anthony Towns",
         "y4": "Jaylen Brown", "y5": "Kyrie Irving", "y6": "Jayson Tatum"}


def _store() -> Store:
    store = Store(":memory:")
    store.upsert_player_logs([
        PlayerGameLog(f"g{i}", SEASON, "2025-11-01", pid, name, "LAL", {"pts": 10.0})
        for i, (pid, name) in enumerate(NBA.items())
    ])
    return store


def _snapshot(**over):
    snap = {
        "league_id": "yahoo-454.l.999", "season": SEASON, "name": "Test League",
        "cadence": "daily-change",
        "teams": [{"team_id": "t1", "name": "A"}, {"team_id": "t2", "name": "B"}],
        "player_names": dict(YAHOO),
        "draft_results": {"t1": ["y1", "y2", "y3"], "t2": ["y4", "y5", "y6"]},
        # t1 traded y3 to t2 for y6 on 11-10
        "final_rosters": {"t1": ["y1", "y2", "y6"], "t2": ["y4", "y5", "y3"]},
        "transactions": [{
            "date": "2025-11-10", "type": "trade",
            "movements": [{"player_id": "y3", "from_team": "t1", "to_team": "t2"},
                          {"player_id": "y6", "from_team": "t2", "to_team": "t1"}],
        }],
        "matchups": [{"period": 1, "start": "2025-10-21", "end": "2025-10-27",
                      "team_a": "t1", "team_b": "t2"},
                     {"period": 4, "start": "2025-11-10", "end": "2025-11-16",
                      "team_a": "t1", "team_b": "t2"}],
    }
    snap.update(over)
    return snap


def test_snapshot_import_yields_correct_point_in_time_rosters():
    store = _store()
    report = import_snapshot(store, _snapshot())

    assert report["verified"] is True
    assert report["players_unresolved"] == 0
    assert report["warnings"] == []

    # before the trade: t1 holds Curry, Jokić, Towns (NBA ids, not Yahoo ids)
    assert store.roster_asof("yahoo-454.l.999", "t1", "2025-11-01") == sorted(
        ["201939", "203999", "1626157"])
    # after the trade: Towns out, Tatum in
    assert store.roster_asof("yahoo-454.l.999", "t1", "2025-11-15") == sorted(
        ["201939", "203999", "1628369"])
    assert store.roster_asof("yahoo-454.l.999", "t2", "2025-11-15") == sorted(
        ["1627759", "202681", "1626157"])


def test_import_refuses_when_a_player_cannot_be_resolved():
    store = _store()
    snap = _snapshot(player_names={**YAHOO, "y9": "Some Unknown Rookie"})
    snap["final_rosters"]["t1"].append("y9")

    with pytest.raises(ImportRefused, match="player id join incomplete"):
        import_snapshot(store, snap)


def test_force_loads_a_degraded_league_and_records_provenance():
    store = _store()
    snap = _snapshot(player_names={**YAHOO, "y9": "Some Unknown Rookie"})
    snap["final_rosters"]["t1"].append("y9")

    report = import_snapshot(store, snap, force=True)
    assert report["players_unresolved"] == 1
    rows = store.conn.execute("SELECT note FROM provenance").fetchall()
    assert any("unmatched" in r["note"] for r in rows)


def test_import_refuses_an_unverifiable_roster_history():
    """A transaction log missing the trade cannot be squared with the draft results."""
    store = _store()
    with pytest.raises(ImportRefused, match="could not be verified"):
        import_snapshot(store, _snapshot(transactions=[]))


def test_normalize_transactions_reads_yahoos_add_drop_shape():
    raw = [{
        "type": "add/drop",
        "timestamp": "1762732800",  # 2025-11-10 UTC
        "players": [
            {"player_id": "6018", "transaction_data": {
                "type": "add", "destination_team_key": "454.l.999.t.3"}},
            {"player_id": "5482", "transaction_data": [{
                "type": "drop", "source_team_key": "454.l.999.t.3"}]},
        ],
    }]
    txns = normalize_transactions(raw)
    assert len(txns) == 1
    tx = txns[0]
    assert tx.date == "2025-11-10"
    add = next(m for m in tx.movements if m.player_id == "6018")
    drop = next(m for m in tx.movements if m.player_id == "5482")
    assert add.to_team == "454.l.999.t.3" and add.from_team is None
    assert drop.from_team == "454.l.999.t.3" and drop.to_team is None
