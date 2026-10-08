"""Yahoo per-player status ingest (dds 2.1).

The two ways this can silently misprice a draft are the two the tests pin: a status string
Yahoo invents that we map to a guess (instead of reporting it unmapped), and an unresolved
player name (instead of reporting it). The rest is shape-tolerance on the payload and the
effective-dating contract: a designation takes effect from the day Yahoo published it, not
the day we ingested it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fantasy_gm.data.store import Store
from fantasy_gm.models import Availability, Game, PlayerGameLog
from fantasy_gm.projections.status import (
    ingest_status,
    load_status_file,
    parse_player_metadata,
    rate_factor,
    status_for_pool,
)

KNOWN_FROM = "2026-10-08"


def _epoch(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp())


def _payload(*players: dict) -> dict:
    """A response shaped like Yahoo's: players keyed by index, each a list of fragments."""
    return {
        "fantasy_content": {
            "league": [
                {"league_key": "478.l.25733"},
                {"players": {
                    **{
                        str(i): {"player": [
                            [{"player_key": f"478.p.{p['id']}"}, {"player_id": p["id"]},
                             {"name": {"full": p["name"]}}],
                            {"status_full": p.get("status", "")},
                            {"injury_note": p.get("note", "")},
                            {"player_notes_last_timestamp": p.get("ts", "")},
                        ]}
                        for i, p in enumerate(players)
                    },
                    "count": len(players),
                }},
            ]
        }
    }


def _store_with(*names: str) -> Store:
    store = Store(":memory:")
    for i, name in enumerate(names):
        gid = f"g{i}"
        store.upsert_games([Game(gid, "2025-26", "2025-11-01", "AAA", "BBB")])
        store.upsert_player_logs(
            [PlayerGameLog(gid, "2025-26", "2025-11-01", f"nba-{i}", name, "AAA", {"pts": 10.0})]
        )
    return store


# --- parsing (pure, offline) -------------------------------------------------


def test_parse_extracts_status_note_and_timestamp():
    rows = parse_player_metadata(_payload(
        {"id": "6017", "name": "Victor Wembanyama",
         "status": "Out (short-term injury)", "note": "Sprained ankle, out 2-3 weeks",
         "ts": str(_epoch("2026-09-30"))},
        {"id": "5583", "name": "Nikola Jokić"},
    ))
    by_name = {r.name: r for r in rows}
    assert by_name["Victor Wembanyama"].status_full == "Out (short-term injury)"
    assert by_name["Victor Wembanyama"].injury_note == "Sprained ankle, out 2-3 weeks"
    assert by_name["Victor Wembanyama"].notes_ts == _epoch("2026-09-30")
    assert by_name["Nikola Jokić"].status_full == ""
    assert by_name["Nikola Jokić"].notes_ts is None


def test_parse_of_an_unrecognised_shape_yields_nothing_rather_than_raising():
    assert parse_player_metadata({"fantasy_content": {"league": []}}) == []
    assert parse_player_metadata({}) == []


def test_effective_date_uses_the_note_timestamp_and_falls_back_to_the_ingest_date():
    # _flatten's stack reverses players, so select by name, not by index.
    rows = parse_player_metadata(_payload(
        {"id": "1", "name": "A", "ts": str(_epoch("2026-09-30"))},
        {"id": "2", "name": "B"},
    ))
    by_name = {r.name: r for r in rows}
    assert effective_date_of(by_name["A"]) == "2026-09-30"
    assert effective_date_of(by_name["B"]) == KNOWN_FROM


def effective_date_of(row):
    from fantasy_gm.projections.status import effective_date

    return effective_date(row, KNOWN_FROM)


# --- ingestion ---------------------------------------------------------------


def test_ingest_stores_designations_effective_dated_and_skips_the_healthy():
    store = _store_with("Victor Wembanyama", "Nikola Jokic")
    result = ingest_status(store, parse_player_metadata(_payload(
        {"id": "6017", "name": "Victor Wembanyama",
         "status": "Out (short-term injury)", "note": "ankle", "ts": str(_epoch("2026-09-30"))},
        {"id": "5583", "name": "Nikola Jokić"},
    )), KNOWN_FROM)

    assert result.stored == 1 and result.rows == 2
    assert result.designations == {"OUT": 1}
    # effective-dated: nothing before the note timestamp, present from it onward
    assert store.availability_asof("nba-0", "2026-09-29") is None
    row = store.availability_asof("nba-0", "2026-10-01")
    assert row is not None
    assert row.status == "OUT" and row.source == "yahoo"
    assert row.known_from == "2026-09-30" and row.note == "ankle"


def test_reingesting_the_same_designation_is_a_no_op():
    store = _store_with("Victor Wembanyama")
    rows = parse_player_metadata(_payload(
        {"id": "6017", "name": "Victor Wembanyama",
         "status": "Out (short-term injury)", "ts": str(_epoch("2026-09-30"))}))
    first = ingest_status(store, rows, KNOWN_FROM)
    second = ingest_status(store, rows, KNOWN_FROM)
    assert first.stored == second.stored == 1
    assert store.conn.execute(
        "SELECT COUNT(*) c FROM availability").fetchone()["c"] == 1


def test_an_unknown_status_is_reported_and_not_stored():
    """A guessed status is a wrong status: Yahoo's wording must surface, not map."""
    store = _store_with("Victor Wembanyama")
    result = ingest_status(store, parse_player_metadata(_payload(
        {"id": "6017", "name": "Victor Wembanyama", "status": "Out To Lunch"},
    )), KNOWN_FROM)
    assert result.stored == 0
    assert result.unmapped == (("Victor Wembanyama", "Out To Lunch"),)
    assert store.availability_asof("nba-0", KNOWN_FROM) is None


def test_a_roster_state_is_ignored_and_reported_not_mapped():
    """'Not Active' is roster state, not health: pricing on it would zero a G-League
    stash player for a reason availability does not own — report it, store nothing."""
    store = _store_with("Victor Wembanyama")
    result = ingest_status(store, parse_player_metadata(_payload(
        {"id": "6017", "name": "Victor Wembanyama", "status": "Not Active"},
    )), KNOWN_FROM)
    assert result.stored == 0
    assert result.ignored == ("Victor Wembanyama",)
    assert result.unmapped == ()          # a known non-health state is not "unmapped"
    assert store.availability_asof("nba-0", KNOWN_FROM) is None


def test_probable_maps_to_active_which_never_raises_a_rate():
    """NBA 'Probable' is the mildest designation — the player plays; store it as ACTIVE
    (a record, not a cap: rate_factor has no ACTIVE entry, so nothing is raised)."""
    store = _store_with("Victor Wembanyama")
    result = ingest_status(store, parse_player_metadata(_payload(
        {"id": "6017", "name": "Victor Wembanyama", "status": "Probable"},
    )), KNOWN_FROM)
    assert result.stored == 1
    assert result.designations == {"ACTIVE": 1}
    row = store.availability_asof("nba-0", KNOWN_FROM)
    assert row is not None and row.status == "ACTIVE"
    assert rate_factor(row) == 1.0


def test_an_unresolved_name_is_reported_not_dropped():
    store = _store_with("Victor Wembanyama")
    result = ingest_status(store, parse_player_metadata(_payload(
        {"id": "9999", "name": "Nobody Known", "status": "Out (short-term injury)"},
    )), KNOWN_FROM)
    assert result.stored == 0
    assert result.unresolved == ("Nobody Known",)


def test_a_dated_note_without_a_designation_is_reported():
    """An injury note with no status_full is exactly the interesting edge — surface it."""
    store = _store_with("Victor Wembanyama")
    result = ingest_status(store, parse_player_metadata(_payload(
        {"id": "6017", "name": "Victor Wembanyama", "note": "sore knee"},
    )), KNOWN_FROM)
    assert result.stored == 0
    assert result.noted == ("Victor Wembanyama",)


# --- the rate-factor convention ----------------------------------------------


def test_season_ending_out_zeroes_the_rate_and_other_out_halves_it():
    season = Availability("p", "OUT", KNOWN_FROM, "yahoo", 1.0, "")
    short = Availability("p", "OUT", KNOWN_FROM, "yahoo", 0.9, "")
    assert rate_factor(season) == 0.0
    assert rate_factor(short) == 0.5


def test_questionable_reduces_and_active_never_raises():
    q = Availability("p", "QUESTIONABLE", KNOWN_FROM, "yahoo", 0.9, "")
    a = Availability("p", "ACTIVE", KNOWN_FROM, "yahoo", 0.9, "")
    assert rate_factor(q) == 0.75
    assert rate_factor(a) == 1.0


def test_the_confidence_marker_only_binds_to_yahoo_rows():
    """A hand-entered OUT row at full confidence must not read as season-ending."""
    manual = Availability("p", "OUT", KNOWN_FROM, "beat-writer:shams", 1.0, "")
    assert rate_factor(manual) == 0.5


# --- batch as-of read --------------------------------------------------------


def test_status_for_pool_reads_the_latest_designation_before_as_of():
    store = _store_with("Victor Wembanyama", "Nikola Jokic", "Steph Curry")
    ingest_status(store, parse_player_metadata(_payload(
        {"id": "6017", "name": "Victor Wembanyama", "status": "Out (short-term injury)",
         "ts": str(_epoch("2026-09-30"))},
        {"id": "5583", "name": "Nikola Jokić", "status": "Questionable",
         "ts": str(_epoch("2026-10-02"))},
        {"id": "1", "name": "Steph Curry", "status": "Out For Season",
         "ts": str(_epoch("2026-09-20"))},
    )), KNOWN_FROM)

    statuses = status_for_pool(store, ["nba-0", "nba-1", "nba-2"], "2026-10-08")
    assert statuses["nba-0"].status == "OUT"
    assert statuses["nba-1"].status == "QUESTIONABLE"
    assert statuses["nba-2"].status == "OUT"
    assert statuses["nba-2"].confidence == 1.0  # season-ending marker

    # gated: nothing known later than as_of leaks in
    early = status_for_pool(store, ["nba-1"], "2026-10-01")
    assert early["nba-1"] is None


def test_status_for_pool_with_no_ids_or_no_rows_is_all_none():
    store = _store_with("Victor Wembanyama")
    assert status_for_pool(store, [], KNOWN_FROM) == {}
    statuses = status_for_pool(store, ["nba-0"], KNOWN_FROM)
    assert statuses == {"nba-0": None}


# --- file + network ----------------------------------------------------------


def test_saved_paged_capture_loads(tmp_path):
    payload = {"pages": [_payload({"id": "1", "name": "A", "status": "Questionable"}),
                         _payload({"id": "2", "name": "B", "status": "Out (short-term injury)"})]}
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(payload))
    rows = load_status_file(path)
    assert {r.name for r in rows} == {"A", "B"}


def test_live_fetch_stops_on_empty_page_and_saves(tmp_path, monkeypatch):
    """Pagination stops on the empty tail page; the capture round-trips through disk."""
    captured_urls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass

    def fake_get(url, headers=None, timeout=None):
        captured_urls.append(url)
        start = int(url.split("start=")[1].split(";")[0])
        if start >= 50:
            return FakeResponse({})
        return FakeResponse(_payload(
            {"id": str(1000 + start), "name": f"Player {start}",
             "status": "Questionable" if start == 0 else ""}
        ))

    monkeypatch.setattr("requests.get", fake_get)
    save = tmp_path / "capture.json"
    rows = fetch_metadata("478.l.25733", "token", count=25, limit=500, save_path=save)
    assert len(rows) == 2
    assert [u.split("start=")[1].split(";")[0] for u in captured_urls] == ["0", "25", "50"]
    again = load_status_file(save)
    assert [(r.name, r.status_full) for r in again] == \
        [(r.name, r.status_full) for r in rows]


def fetch_metadata(league_key, token, *, count, limit, save_path):
    from fantasy_gm.projections.status import fetch_league_player_metadata

    return fetch_league_player_metadata(league_key, token, count=count, limit=limit,
                                        save_path=save_path)
