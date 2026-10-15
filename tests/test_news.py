"""Multi-source news ingest + event-driven surfacing (R10, section 9).

The tests pin the ways this module could silently misprice a draft: a page status
mapped to a guess instead of reported unmapped; a web row claiming the API-only
season-ending certainty; an ambiguous headline resolved to a coin flip; a stale wire
that nobody can see is stale. Parsing runs against the saved capture of the real
injuries page, so a Yahoo shape change fails here before it fails draft night.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fantasy_gm.data.player_index import IndexEntry
from fantasy_gm.data.store import Store
from fantasy_gm.models import Availability, ForwardRoster, Game, PlayerGameLog
from fantasy_gm.projections.news import (
    INJURIES_URL,
    flags_for_pool,
    ingest_yahoo_injuries,
    ingest_yahoo_news,
    load_injuries_file,
    normalize_web_status,
    page_date_to_iso,
    parse_yahoo_rss,
    roster_move_events,
    source_age,
)
from fantasy_gm.projections.status import (
    OUT,
    QUESTIONABLE,
    is_season_out,
    rate_factor,
    season_out_ahead,
    status_for_pool,
)

FIXTURE = Path(__file__).parent / "fixtures" / "yahoo_injuries_page_fragment.html"
TODAY = date(2026, 10, 15)

RSS_XML = """<?xml version="1.0"?>
<rss><channel>
<item><title>Rash Report: Tyrese Haliburton (quad) trending toward return</title>
<link>https://sports.yahoo.com/nba/news/1</link><pubDate>Wed, 14 Oct 2026 18:30:00 +0000</pubDate>
<description>Pacers update on Haliburton.</description></item>
<item><title>Jalen Johnson listed questionable for opener</title>
<link>https://sports.yahoo.com/nba/news/2</link><pubDate>Wed, 14 Oct 2026 20:00:00 +0000</pubDate>
<description>Hawks forward day-to-day.</description></item>
<item><title>League announces new replay rule for 2026-27</title>
<link>https://sports.yahoo.com/nba/news/3</link><pubDate>Tue, 13 Oct 2026 09:00:00 +0000</pubDate>
<description>No player named.</description></item>
</channel></rss>"""


def _store_with(*names: str) -> Store:
    store = Store(":memory:")
    for i, name in enumerate(names):
        gid = f"g{i}"
        store.upsert_games([Game(gid, "2025-26", "2025-11-01", "AAA", "BBB")])
        store.upsert_player_logs(
            [PlayerGameLog(gid, "2025-26", "2025-11-01", f"nba-{i}", name, "AAA", {"pts": 10.0})]
        )
    return store


# --- parsing the saved capture -------------------------------------------------


def test_parse_yahoo_injuries_page_extracts_rows():
    rows = load_injuries_file(FIXTURE)
    by_name = {r.name: r for r in rows}
    assert "CJ McCollum" in by_name
    cj = by_name["CJ McCollum"]
    assert cj.pos == "PG"
    assert cj.status_full == "Questionable (Rest)"
    assert cj.date_md == "10/14"
    assert cj.web_id == "5161"
    # the repeated section header row carries no player link and is skipped
    assert all(r.name for r in rows)


def test_page_date_resolves_most_recent_occurrence():
    # 10/14 with today 10-15 is yesterday; 7/15 is this July, not next July
    assert page_date_to_iso("10/14", TODAY) == "2026-10-14"
    assert page_date_to_iso("7/15", TODAY) == "2026-07-15"
    assert page_date_to_iso("12/1", TODAY) == "2025-12-01"
    assert page_date_to_iso("3/2/2025", TODAY) == "2025-03-02"
    # unparseable falls back to today rather than crashing an ingest
    assert page_date_to_iso("n/a", TODAY) == TODAY.isoformat()


def test_web_status_folds_to_status_map_keys():
    assert normalize_web_status("Out (ACL)") == "out"
    assert normalize_web_status("Questionable (Rest)") == "questionable"
    assert normalize_web_status("Out For Season") == "out for season"
    # wording found live 2026-10-15: the injuries page annotates even the season-out form
    assert normalize_web_status("Out For The Season (Achilles)") == "out for the season"
    assert normalize_web_status("Day-to-Day") == "day-to-day"
    # something genuinely new stays itself -> reported unmapped at ingest, never guessed
    assert normalize_web_status("Hospitalized") == "hospitalized"


def test_ingest_page_stores_plain_factor_rows_and_events():
    store = _store_with("CJ McCollum", "Henri Veesaar", "Onyeka Okongwu")
    rows = load_injuries_file(FIXTURE)
    result = ingest_yahoo_injuries(store, rows, "2026-10-15", today="2026-10-15")

    assert result.rows > 0
    assert result.stored_designations >= 2
    assert ("CJ McCollum", "Questionable (Rest)") not in result.unmapped
    assert result.stored_events > 0

    statuses = status_for_pool(store, ["nba-0", "nba-1"], "2026-10-15")
    assert statuses["nba-0"].status == QUESTIONABLE
    assert statuses["nba-0"].source == "yahoo-web"
    # Henri Veesaar is "Out (ACL)" on the page — a plain OUT (x0.5), NOT the
    # season-ending certainty. Only the API wording grants rate 0.
    assert statuses["nba-1"].status == OUT
    assert is_season_out(statuses["nba-1"]) is False
    assert rate_factor(statuses["nba-1"]) == 0.5
    # effective date is the page's date, not the ingest date
    assert statuses["nba-1"].known_from == "2026-09-21"

    news = store.news_asof(["nba-1"], "2026-10-15")
    assert news["nba-1"][0].kind == "designation"
    assert news["nba-1"][0].source == "yahoo-web"
    assert news["nba-1"][0].url == INJURIES_URL

    # re-ingesting the same capture is a no-op on both tables
    again = ingest_yahoo_injuries(store, rows, "2026-10-15", today="2026-10-15")
    assert again.stored_events == 0


def test_ingest_page_reports_unmapped_and_unresolved():
    store = _store_with("CJ McCollum")
    from fantasy_gm.projections.news import InjuryPageRow

    rows = [
        InjuryPageRow("CJ McCollum", "PG", "Hospitalized", "10/14"),
        InjuryPageRow("Made-up Person", "SF", "Out (Knee)", "10/14"),
    ]
    result = ingest_yahoo_injuries(store, rows, "2026-10-15", today="2026-10-15")
    assert result.unmapped == (("CJ McCollum", "Hospitalized"),)
    assert result.unresolved == ("Made-up Person",)
    assert result.stored_designations == 0


def test_parse_rss_and_ingest_matches_pool_names():
    items = parse_yahoo_rss(RSS_XML)
    assert len(items) == 3
    assert items[0].published_on == "2026-10-14"

    store = _store_with("Tyrese Haliburton", "Jalen Johnson")
    result = ingest_yahoo_news(store, items, "2026-10-15")
    assert result.stored_events == 2  # the no-player league item is not stored
    assert result.rows == 3

    news = store.news_asof(["nba-0", "nba-1"], "2026-10-15")
    assert "Haliburton" in news["nba-0"][0].headline
    assert news["nba-0"][0].url.endswith("/news/1")
    # re-ingest is a no-op (same source/date/headline key)
    assert ingest_yahoo_news(store, items, "2026-10-15").stored_events == 0


def test_ambiguous_headline_is_reported_not_guessed():
    store = _store_with("Tyrese Haliburton", "Jalen Johnson", "Jalen Williams")
    items = parse_yahoo_rss(
        RSS_XML.replace(
            "Jalen Johnson listed questionable for opener",
            "Jalen Johnson and Jalen Williams both day-to-day",
        )
    )
    result = ingest_yahoo_news(store, items, "2026-10-15")
    assert result.ambiguous == ("ambiguous: Jalen Johnson, Jalen Williams",)
    assert len(result.unresolved) == 1  # the no-player league item
    assert result.stored_events == 1  # only the Haliburton item stored


# --- roster moves (NBA playerindex diff) ---------------------------------------


def test_roster_move_events_from_index_diff():
    before = {
        "1": ForwardRoster("1", "2026-27", "BOS", 1, "2026-10-01"),
        "2": ForwardRoster("2", "2026-27", "LAL", 4, "2026-10-01"),
    }
    entries = [
        IndexEntry("1", "Traded Player", "DAL", "F", 1),  # BOS -> DAL
        IndexEntry("2", "Waived Player", "", "F", 0),  # off rosters
        IndexEntry("3", "Brand New", "MIA", "G", 1),  # not stored before: skip
    ]
    events = roster_move_events(before, entries, "2026-10-15")
    by_pid = {e.player_id: e for e in events}
    assert by_pid["1"].headline == "Traded Player: BOS -> DAL"
    assert by_pid["1"].kind == "roster-move"
    assert "no longer on an NBA roster" in by_pid["2"].headline
    assert "3" not in by_pid


# --- the season-OUT promotion rule ---------------------------------------------


def _promotion_store() -> Store:
    """One team: starter (rank 1) OUT for season, backup behind him at rank 2,
    an unrelated third teammate at rank 3 on another team's row set."""
    store = _store_with("Starter Guy", "Backup Guy", "Healthy Guy")
    store.add_forward_roster(
        [
            ForwardRoster("nba-0", "2026-27", "AAA", 1, "2026-10-01"),
            ForwardRoster("nba-1", "2026-27", "AAA", 2, "2026-10-01"),
            ForwardRoster("nba-2", "2026-27", "AAA", 3, "2026-10-01"),
        ]
    )
    # the certainty marker (yahoo source, confidence 1.0) is what makes it season-OUT
    store.add_availability(
        [
            Availability("nba-0", "OUT", "2026-10-10", "yahoo", 1.0, "out for season"),
        ]
    )
    return store


def test_season_out_ahead_counts_only_season_out_teammates():
    store = _promotion_store()
    ahead, fwd = season_out_ahead(store, "nba-1", "2026-27", "2026-10-15")
    assert ahead == 1 and fwd.depth_chart_pos == 2
    # the OUT player himself is not promoted by his own designation
    assert season_out_ahead(store, "nba-0", "2026-27", "2026-10-15")[0] == 0
    # a QUESTIONABLE teammate does not promote (plain OUT wouldn't either)
    store.add_availability(
        [
            Availability("nba-2", "OUT", "2026-10-12", "yahoo", 0.9, "short-term"),
        ]
    )
    assert season_out_ahead(store, "nba-1", "2026-27", "2026-10-15")[0] == 1
    # invisible before the designation existed (effective-dating)
    assert season_out_ahead(store, "nba-1", "2026-27", "2026-10-09")[0] == 0


def test_flags_surface_designation_news_and_promotion():
    store = _promotion_store()
    store.add_news_events([])  # ensure the table exists
    flags = flags_for_pool(
        store, ["nba-0", "nba-1", "nba-2"], "2026-10-15", forward_season="2026-27"
    )
    assert flags["nba-0"] == "OUT"
    # both backups sit behind the season-OUT starter, so both are promoted by one
    assert flags["nba-1"] == "+1d"
    assert flags["nba-2"] == "+1d"


def test_flags_count_wire_news():
    store = _store_with("Tyrese Haliburton")
    store.add_news_events(
        [
            __import__("fantasy_gm.models", fromlist=["NewsEvent"]).NewsEvent(
                source="yahoo-news",
                published_on="2026-10-14",
                kind="news",
                headline="Tyrese Haliburton practicing fully",
                player_id="nba-0",
                name="Tyrese Haliburton",
            ),
        ]
    )
    flags = flags_for_pool(store, ["nba-0"], "2026-10-15")
    assert flags["nba-0"] == "1n"


def test_source_age_reports_every_stored_source():
    store = _store_with("CJ McCollum")
    rows = load_injuries_file(FIXTURE)
    ingest_yahoo_injuries(store, rows, "2026-10-15", today="2026-10-15")
    ages = source_age(store, "2026-10-15")
    by_source = {a["source"]: a for a in ages}
    assert by_source["yahoo-web"]["events"] > 0
    assert by_source["yahoo-web"]["latest"] == "2026-10-14"
    # as-of honesty: nothing is visible from before the rows were known
    assert source_age(store, "2026-01-01") == []


def test_x_stub_is_loud():
    import pytest

    from fantasy_gm.projections.news import fetch_x_events

    with pytest.raises(RuntimeError, match="stub"):
        fetch_x_events("shams")


def test_derived_promotion_reprices_only_when_enabled():
    """Task 9.6 end-to-end at the source level: the projected basis re-prices the
    backup's role from the season-OUT teammate; the default path does not."""
    from fantasy_gm.projections.derived import DerivedProjectionSource

    store = _promotion_store()
    # give the backup a real sample so the modeled path runs
    for i in range(12):
        gid = f"g{i}"
        store.upsert_games([Game(gid, "2025-26", f"2025-11-{i + 1:02d}", "AAA", "BBB")])
        store.upsert_player_logs(
            [
                PlayerGameLog(
                    gid,
                    "2025-26",
                    f"2025-11-{i + 1:02d}",
                    "nba-1",
                    "Backup Guy",
                    "AAA",
                    {
                        "pts": 10.0,
                        "reb": 4.0,
                        "ast": 2.0,
                        "stl": 1.0,
                        "blk": 0.5,
                        "fgm": 4.0,
                        "fga": 9.0,
                        "ftm": 2.0,
                        "fta": 2.5,
                        "tov": 1.0,
                    },
                ),
            ]
        )
    plain = DerivedProjectionSource(store).project("2026-27", "2026-10-15", player_ids=["nba-1"])[
        "nba-1"
    ]
    promoted = DerivedProjectionSource(
        store,
        season_out_promotion=True,
    ).project("2026-27", "2026-10-15", player_ids=["nba-1"])["nba-1"]
    assert "depth_promotion" not in plain.notes
    assert promoted.notes.get("depth_promotion") == "+1"
    assert promoted.notes.get("stated_depth") == "2"  # the stated rank itself is intact
    # The effective rank feeds the model through the same variable that carries
    # stated_depth into MinutesModel and tier_of; this 1-game fixture's minutes fit is
    # degenerate (0.0 both ways), so the re-pricing itself is pinned by season_out_ahead.
