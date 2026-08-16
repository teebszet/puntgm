"""Forward-season roster inputs from NBA `playerindex`.

This ingest is what makes the projection react to an offseason move at all, so the properties
worth pinning are that a team change actually re-ranks a player, that players with no NBA
history reach the draft pool, and that nothing is fabricated for a player who has no job.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fantasy_gm.data.cache import RawCache
from fantasy_gm.data.player_index import (
    build_forward_roster,
    ingest_player_index,
    parse_player_index,
    to_incoming_players,
    to_positions,
)
from fantasy_gm.data.store import Store
from fantasy_gm.models import PlayerPosition
from fantasy_gm.projections.derived import DerivedProjectionSource
from tests.test_projection_model import (
    DRAFT_DAY,
    KNOWN_FROM,
    PER_TEAM,
    SEASON,
    START,
    _seed_league,
)


def _row(pid: str, first: str, last: str, team: str, position: str = "G",
         status: int = 1, **extra) -> dict:
    row = {
        "PERSON_ID": pid, "PLAYER_FIRST_NAME": first, "PLAYER_LAST_NAME": last,
        "TEAM_ABBREVIATION": team, "POSITION": position, "ROSTER_STATUS": status,
        "DRAFT_YEAR": "", "DRAFT_ROUND": "", "DRAFT_NUMBER": "",
        "FROM_YEAR": "", "TO_YEAR": "",
    }
    row.update(extra)
    return row


@pytest.fixture
def store() -> Store:
    s = Store(":memory:")
    _seed_league(s)
    return s


# --- parsing (pure, offline) -------------------------------------------------


def test_parse_maps_the_fields_the_store_actually_needs():
    entries = parse_player_index([
        _row("203999", "Nikola", "Jokić", "DEN", "C", DRAFT_NUMBER="41"),
        _row("1641705", "Zaccharie", "Risacher", "ATL", "F", DRAFT_NUMBER="1"),
    ])
    assert [e.player_name for e in entries] == ["Nikola Jokić", "Zaccharie Risacher"]
    assert entries[0].position == "C" and entries[0].team == "DEN"
    assert entries[0].is_rostered


def test_parse_skips_rows_with_no_person_id():
    assert parse_player_index([{"PLAYER_LAST_NAME": "Nobody"}]) == []


def test_an_unrostered_player_is_parsed_but_not_treated_as_having_a_job():
    """A free agent is real information; putting them on a forward roster is not."""
    entries = parse_player_index([_row("1", "Free", "Agent", "", status=0)])
    assert len(entries) == 1
    assert not entries[0].is_rostered


def test_a_multi_position_listing_splits_into_slots():
    assert PlayerPosition("p", "G-F", KNOWN_FROM).slots() == ("G", "F")
    assert PlayerPosition("p", "C", KNOWN_FROM).slots() == ("C",)


def test_positions_skip_players_with_no_listing():
    entries = parse_player_index([_row("1", "A", "B", "DEN", position=""),
                                  _row("2", "C", "D", "DEN", position="F")])
    assert [p.player_id for p in to_positions(entries, KNOWN_FROM)] == ["2"]


# --- incoming players (D9) ---------------------------------------------------


def test_a_player_with_no_game_logs_becomes_an_incoming_player(store):
    """The draft pool derives from logs, so without this a rookie is undraftable."""
    entries = parse_player_index([
        _row("rookie", "Rook", "Ie", "AAA", "F", DRAFT_NUMBER="3"),
        _row("AAA-0", "Known", "Player", "AAA", "G"),
    ])
    incoming = to_incoming_players(store, entries, SEASON, KNOWN_FROM)
    assert [p.player_id for p in incoming] == ["rookie"]
    assert incoming[0].draft_pick == 3

    store.add_incoming_players(incoming)
    assert "rookie" in store.draft_pool_asof(SEASON, DRAFT_DAY)


def test_membership_is_decided_by_missing_logs_not_by_draft_year(store):
    """A two-year-old second-rounder who has still never played needs the prior path just as
    much as this year's number one."""
    entries = parse_player_index(
        [_row("stashed", "Sta", "Shed", "AAA", "C", DRAFT_YEAR="2024", DRAFT_NUMBER="55")]
    )
    assert [p.player_id for p in to_incoming_players(store, entries, SEASON, KNOWN_FROM)] \
        == ["stashed"]


def test_an_unrostered_player_without_logs_is_not_made_draftable(store):
    entries = parse_player_index([_row("nobody", "No", "Body", "", status=0)])
    assert to_incoming_players(store, entries, SEASON, KNOWN_FROM) == []


# --- the derived depth chart (A-DRAFT-12) ------------------------------------


def test_depth_is_ranked_by_the_new_roster_s_own_minutes_history(store):
    """`playerindex` says where a player is, not where they sit; rank comes from what the
    incoming teammates have actually earned."""
    entries = parse_player_index(
        [_row(f"AAA-{i}", "P", str(i), "AAA") for i in range(PER_TEAM)]
    )
    rosters = build_forward_roster(store, entries, SEASON, KNOWN_FROM, as_of=DRAFT_DAY)
    by_id = {r.player_id: r for r in rosters}
    # The seed gives AAA-0 the most minutes and AAA-9 the fewest.
    assert by_id["AAA-0"].depth_chart_pos < by_id["AAA-9"].depth_chart_pos
    assert {r.team for r in rosters} == {"AAA"}


def test_signing_with_a_deeper_team_lowers_the_depth_and_the_projection(store):
    """The whole point of the ingest: an offseason move has to move the projection, with no
    manual entry and no external depth chart."""
    stays = parse_player_index(
        [_row(f"AAA-{i}", "P", str(i), "AAA") for i in range(PER_TEAM)]
    )
    # AAA-8 (a deep bench player on AAA) instead joins BBB, where he is behind everyone.
    moves = [e for e in stays if e.player_id != "AAA-8"] + parse_player_index(
        [_row("AAA-8", "P", "8", "BBB")]
        + [_row(f"BBB-{i}", "P", str(i), "BBB") for i in range(PER_TEAM)]
    )

    for entries, tag in ((stays, "stay"), (moves, "move")):
        s = Store(":memory:")
        _seed_league(s)
        s.add_forward_roster(
            build_forward_roster(s, entries, SEASON, KNOWN_FROM, as_of=DRAFT_DAY)
        )
        proj = DerivedProjectionSource(s).project(SEASON, DRAFT_DAY, ["AAA-8"])["AAA-8"]
        if tag == "stay":
            stay_rank, stay_min = proj.notes["stated_depth"], float(proj.notes["minutes"])
        else:
            move_rank, move_min = proj.notes["stated_depth"], float(proj.notes["minutes"])
            assert proj.notes.get("team_changed") == "yes"

    assert int(move_rank) > int(stay_rank)
    assert move_min < stay_min


def test_a_player_with_no_history_ranks_last_rather_than_first(store):
    """Ranking on an empty history must not accidentally promote a rookie over the roster."""
    entries = parse_player_index(
        [_row("rookie", "Rook", "Ie", "AAA", DRAFT_NUMBER="3")]
        + [_row(f"AAA-{i}", "P", str(i), "AAA") for i in range(PER_TEAM)]
    )
    rosters = build_forward_roster(store, entries, SEASON, KNOWN_FROM, as_of=DRAFT_DAY)
    by_id = {r.player_id: r for r in rosters}
    assert by_id["rookie"].depth_chart_pos == max(r.depth_chart_pos for r in rosters)
    assert by_id["rookie"].role == "no-history"
    assert by_id["AAA-0"].role == "returning"


def test_the_derived_depth_chart_reads_no_games_after_the_history_cut(store):
    """The ingest is dated like everything else: ranking cannot use games from the season
    being projected."""
    early = build_forward_roster(
        store, parse_player_index([_row(f"AAA-{i}", "P", str(i), "AAA") for i in range(3)]),
        SEASON, KNOWN_FROM, as_of=(START + timedelta(days=2)).isoformat(),
    )
    assert all(r.known_from == KNOWN_FROM for r in early)
    # Too few games to clear min_games, so nobody has a usable claim on a rank yet.
    assert all(r.role == "no-history" for r in early)


# --- end to end --------------------------------------------------------------


def test_ingest_writes_three_tables_and_dry_run_writes_none(store, tmp_path):
    cache = RawCache(tmp_path)
    cache.set("playerindex", {"season": SEASON}, {"PlayerIndex": [
        _row("AAA-0", "P", "0", "AAA", "G"),
        _row("AAA-1", "P", "1", "AAA", "F"),
        _row("rookie", "Rook", "Ie", "AAA", "C", DRAFT_NUMBER="3"),
        _row("freeagent", "Free", "Agent", "", "G", status=0),
    ]})

    dry = ingest_player_index(store, SEASON, cache, KNOWN_FROM, dry_run=True, as_of=DRAFT_DAY)
    assert dry["rows"] == 4 and dry["rostered"] == 3
    assert store.player_positions_asof(DRAFT_DAY) == {}
    assert store.forward_roster_asof("AAA-0", SEASON, DRAFT_DAY) is None

    counts = ingest_player_index(store, SEASON, cache, KNOWN_FROM, as_of=DRAFT_DAY)
    assert counts == dry
    assert store.player_positions_asof(DRAFT_DAY)["AAA-1"].position == "F"
    assert store.forward_roster_asof("AAA-0", SEASON, DRAFT_DAY).team == "AAA"
    assert [p.player_id for p in store.incoming_players_asof(SEASON, DRAFT_DAY)] == ["rookie"]


def test_ingested_inputs_are_invisible_before_the_date_they_were_known(store, tmp_path):
    cache = RawCache(tmp_path)
    cache.set("playerindex", {"season": SEASON}, {"PlayerIndex": [_row("AAA-0", "P", "0", "AAA")]})
    ingest_player_index(store, SEASON, cache, KNOWN_FROM, as_of=DRAFT_DAY)
    assert store.forward_roster_asof("AAA-0", SEASON, "2026-07-01") is None
    assert store.player_positions_asof("2026-07-01") == {}


def test_positions_reach_the_store_keyed_to_store_player_ids(store, tmp_path):
    """The slot-assignment problem (D4) needs positions keyed the same way as everything
    else; a position table keyed to NBA ids nobody else uses would be useless."""
    cache = RawCache(tmp_path)
    cache.set("playerindex", {"season": SEASON},
              {"PlayerIndex": [_row("AAA-0", "P", "0", "AAA", "G-F")]})
    ingest_player_index(store, SEASON, cache, KNOWN_FROM, as_of=DRAFT_DAY)
    positions = store.player_positions_asof(DRAFT_DAY)
    assert set(positions) <= {pid for pid, _n, _t in store.player_universe("2025-26")}
    assert positions["AAA-0"].slots() == ("G", "F")


def test_missing_position_is_absent_rather_than_a_default(store, tmp_path):
    cache = RawCache(tmp_path)
    cache.set("playerindex", {"season": SEASON}, {"PlayerIndex": [
        _row("AAA-0", "P", "0", "AAA", "G"), _row("AAA-1", "P", "1", "AAA", position=""),
    ]})
    ingest_player_index(store, SEASON, cache, KNOWN_FROM, as_of=DRAFT_DAY)
    positions = store.player_positions_asof(DRAFT_DAY)
    assert "AAA-0" in positions and "AAA-1" not in positions
