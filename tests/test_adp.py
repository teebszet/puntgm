"""ADP ingestion from Yahoo draft_analysis (task 2.4).

Yahoo's JSON is a nested mix of lists and dicts rather than a flat object, and the store keys
by NBA player id while Yahoo keys by its own — so the two things worth pinning are that the
parse survives the shape, and that nothing gets silently dropped or silently defaulted on the
way in. A star that fails to resolve is a mispriced draft; an invented draft position is
worse than a missing one.
"""

from __future__ import annotations

import json

import pytest

from fantasy_gm.data.store import Store
from fantasy_gm.models import Game, PlayerGameLog
from fantasy_gm.projections.adp import (
    adp_for_pool,
    build_name_resolver,
    fetch_draft_analysis,
    ingest_adp,
    ingest_adp_file,
    normalize_name,
    parse_draft_analysis,
)

SEASON = "2026-27"
KNOWN_FROM = "2026-09-25"


def _payload(*players: dict) -> dict:
    """A response shaped like Yahoo's: players keyed by index, each a list of fragments."""
    return {
        "fantasy_content": {
            "league": [
                {"league_key": "466.l.1"},
                {"players": {
                    **{
                        str(i): {"player": [
                            [{"player_key": f"466.p.{p['id']}"}, {"player_id": p["id"]},
                             {"name": {"full": p["name"], "first": p["name"].split()[0],
                                       "last": p["name"].split()[-1]}}],
                            {"draft_analysis": [
                                {"average_pick": p.get("pick", "")},
                                {"average_round": p.get("round", "")},
                                {"percent_drafted": p.get("pct", "")},
                            ]},
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


def test_parse_extracts_pick_and_percent_from_the_nested_shape():
    rows = parse_draft_analysis(_payload(
        {"id": "5583", "name": "Nikola Jokić", "pick": "1.4", "round": "1", "pct": "100"},
        {"id": "6017", "name": "Victor Wembanyama", "pick": "3.9", "pct": "99"},
    ))
    assert [r.name for r in rows] == ["Nikola Jokić", "Victor Wembanyama"] or \
           {r.name for r in rows} == {"Nikola Jokić", "Victor Wembanyama"}
    by_name = {r.name: r for r in rows}
    assert by_name["Nikola Jokić"].average_pick == pytest.approx(1.4)
    assert by_name["Nikola Jokić"].yahoo_id == "5583"
    assert by_name["Victor Wembanyama"].percent_drafted == pytest.approx(99.0)


def test_parse_keeps_a_player_the_market_has_not_priced():
    """An unpriced player is information. Dropping the row at parse time would hide it."""
    rows = parse_draft_analysis(_payload({"id": "1", "name": "Deep Bench", "pick": ""}))
    assert len(rows) == 1
    assert rows[0].average_pick is None


def test_parse_of_an_unrecognised_shape_yields_nothing_rather_than_raising():
    assert parse_draft_analysis({"fantasy_content": {"league": []}}) == []


# --- identity resolution -----------------------------------------------------


def test_names_fold_across_accents_punctuation_and_suffixes():
    assert normalize_name("Nikola Jokić") == normalize_name("Nikola Jokic")
    assert normalize_name("Jaren Jackson Jr.") == normalize_name("Jaren Jackson")
    assert normalize_name("De'Aaron Fox") == normalize_name("DeAaron Fox")


def test_an_ambiguous_name_resolves_to_nothing_rather_than_a_coin_flip():
    """Two players folding to one key is a gap to report, not a 50/50 guess — a wrong id
    silently prices the wrong player."""
    store = _store_with("Jaren Jackson", "Jaren Jackson Jr.")
    resolve = build_name_resolver(store)
    assert resolve("Jaren Jackson") is None


def test_resolution_maps_yahoo_names_onto_store_player_ids():
    store = _store_with("Nikola Jokic")
    assert build_name_resolver(store)("Nikola Jokić") == "nba-0"


# --- ingestion ---------------------------------------------------------------


def test_ingest_stores_resolved_rows_and_reports_the_rest():
    store = _store_with("Nikola Jokic")
    result = ingest_adp(store, parse_draft_analysis(_payload(
        {"id": "1", "name": "Nikola Jokić", "pick": "1.4", "pct": "100"},
        {"id": "2", "name": "Nobody Known", "pick": "40.1", "pct": "55"},
        {"id": "3", "name": "Deep Bench", "pick": ""},
    )), SEASON, KNOWN_FROM)

    assert result.stored == 1 and result.rows == 3
    assert result.unresolved == ("Nobody Known",)
    assert result.missing_adp == ("Deep Bench",)

    stored = store.adp_asof(SEASON, "2026-10-01")
    assert stored["nba-0"].adp == pytest.approx(1.4)
    assert stored["nba-0"].pct_drafted == pytest.approx(1.0)  # 100 normalised to a fraction


def test_ingested_adp_is_effective_dated():
    store = _store_with("Nikola Jokic")
    ingest_adp(store, parse_draft_analysis(
        _payload({"id": "1", "name": "Nikola Jokic", "pick": "1.4"})), SEASON, KNOWN_FROM)
    assert store.adp_asof(SEASON, "2026-09-01") == {}
    assert "nba-0" in store.adp_asof(SEASON, "2026-10-01")


def test_ingest_from_a_saved_payload_file(tmp_path):
    store = _store_with("Nikola Jokic")
    path = tmp_path / "draft_analysis.json"
    path.write_text(json.dumps(_payload({"id": "1", "name": "Nikola Jokic", "pick": "2.2"})))
    result = ingest_adp_file(store, path, SEASON, KNOWN_FROM)
    assert result.stored == 1


def test_the_live_fetch_names_its_missing_dependency():
    """A stub that raises is a visible gap; one that returns [] is a silent one."""
    with pytest.raises(NotImplementedError, match="4.1"):
        fetch_draft_analysis("466.l.1")


# --- explicit absence --------------------------------------------------------


def test_a_player_with_no_adp_is_absent_not_defaulted():
    store = _store_with("Nikola Jokic", "Deep Bench")
    ingest_adp(store, parse_draft_analysis(
        _payload({"id": "1", "name": "Nikola Jokic", "pick": "1.4"})), SEASON, KNOWN_FROM)
    priced = adp_for_pool(store, SEASON, "2026-10-01", ["nba-0", "nba-1"])
    assert priced["nba-0"].adp == pytest.approx(1.4)
    assert priced["nba-1"] is None
