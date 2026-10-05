"""The draft-night surface: state mechanics, manual entry, reconciliation, output."""

from __future__ import annotations

import pytest

from fantasy_gm.draft.live import (
    DraftState,
    Pick,
    _seat_of,
    apply_manual_pick,
    build_player_directory,
    load_state,
    normalize_name,
    parse_draft_results,
    reconcile,
    render_recommendation,
    resolve_player,
    save_state,
)

# --- draft mechanics -----------------------------------------------------------


def test_seat_of_snake_forward_and_back():
    # A snake draft turns at pick 13 for 12 teams: picks 12 and 13 are both seat 12
    # (the team at the turn picks at both the end of one round and the start of the next).
    assert [_seat_of(i, 12) for i in range(1, 6)] == [1, 2, 3, 4, 5]
    assert [_seat_of(i, 12) for i in (11, 12, 13, 14)] == [11, 12, 12, 11]
    assert _seat_of(24, 12) == 1   # last pick of round 2


def test_on_the_clock_and_gap():
    state = DraftState(league_key="t", n_teams=12, my_seat=5)
    assert state.on_the_clock == 1
    assert state.picks_until_my_next() == 4
    for i in range(1, 13):  # fill round 1
        state.picks.append(Pick(number=i, player_id=f"p{i}", team_seat=_seat_of(i, 12),
                                source="manual"))
    assert state.on_the_clock == 12
    # seat 5's next pick is #20 (round 2 reversed order); others pick 13-19 first
    assert state.picks_until_my_next() == 7
    assert not state.is_my_pick()


def test_picks_until_my_next_when_on_the_clock():
    state = DraftState(league_key="t", n_teams=12, my_seat=5)
    for i in range(1, 5):  # picks 1-4 made, seat 5 on the clock
        state.picks.append(Pick(number=i, player_id=f"p{i}", team_seat=i, source="manual"))
    assert state.is_my_pick()
    # our current pick is ours; the next is #20, so 14 other picks happen first
    assert state.picks_until_my_next() == 14


def test_add_pick_derives_seat_and_rejects_duplicates():
    state = DraftState(league_key="t", n_teams=4, my_seat=2)
    state.add_pick("a", "Alice", "manual")
    state.add_pick("b", "Bob", "manual")
    assert state.picks[0].team_seat == 1
    assert state.picks[1].team_seat == 2
    with pytest.raises(ValueError):
        state.add_pick("a", "Alice again", "manual")


def test_roster_and_available():
    state = DraftState(league_key="t", n_teams=4, my_seat=2)
    for pid in ("a", "b", "c"):
        state.add_pick(pid, pid.upper(), "manual")
    assert state.roster_of(2) == ["b"]
    assert state.available(["b", "d"]) == ["d"]


# --- manual entry --------------------------------------------------------------


def test_normalize_name_folds_accents_and_suffixes():
    assert normalize_name("D'Angelo Russell Jr.") == "dangelo russell"
    assert normalize_name("Nikola Jokić") == "nikola jokic"
    assert normalize_name("Monté Morris III") == "monte morris"


def test_resolve_player_exact_prefix_and_ambiguous():
    by_id = {"1": "Nikola Jokic", "2": "Nikola Vucevic", "3": "Jokic Nemesis"}
    by_key = {}
    for pid, name in by_id.items():
        by_key.setdefault(normalize_name(name), []).append(pid)
    assert resolve_player("nikola jokic", by_id, by_key) == ("1", [])
    # "nikola" prefixes both Nikolas -> ambiguous
    assert resolve_player("nikola", by_id, by_key) == (None, ["1", "2"])
    # "nikola j" is a unique prefix of Jokic -> resolves
    assert resolve_player("nikola j", by_id, by_key) == ("1", [])
    # full-name prefix unique
    assert resolve_player("jokic n", by_id, by_key) == ("3", [])


def test_apply_manual_pick_records_and_reports_unknown():
    state = DraftState(league_key="t", n_teams=4, my_seat=1)
    directory = ({"1": "Jalen Duren"}, {"jalen duren": ["1"]})
    pid, cands, err = apply_manual_pick(state, "jalen duren", directory)
    assert pid == "1" and err is None and len(state.picks) == 1
    pid, cands, err = apply_manual_pick(state, "zzz unknown", directory)
    assert pid is None and cands == [] and err is None
    # duplicate pick reports an error, does not record
    pid, cands, err = apply_manual_pick(state, "jalen duren", directory)
    assert pid is None and err is not None and len(state.picks) == 1


# --- reconciliation ------------------------------------------------------------


def test_reconcile_merges_platform_picks_and_reports_conflicts():
    state = DraftState(league_key="t", n_teams=4, my_seat=1)
    state.add_pick("99", "Mystery Man", "manual")  # we think seat 1 took 99
    platform = [
        {"pick": 1, "team_key": "478.l.25733.t.1", "player_id": "99"},
        {"pick": 2, "team_key": "x", "player_id": "42"},
    ]
    issues = reconcile(state, platform, {})
    # pick 1 agrees, pick 2 is new
    assert issues == []
    assert [p.player_id for p in state.picks] == ["99", "42"]
    assert state.picks[1].source == "live"


def test_reconcile_reports_disagreement_and_gap():
    state = DraftState(league_key="t", n_teams=4, my_seat=1)
    state.add_pick("7", "Ours", "manual")
    platform = [
        {"pick": 1, "team_key": "x", "player_id": "8"},   # disagreement
        {"pick": 3, "team_key": "x", "player_id": "9"},   # a gap: we have no pick 2
    ]
    issues = reconcile(state, platform, {"8": "Theirs", "9": "Later"})
    assert any("pick 1: we have Ours" in i for i in issues)
    assert any("gap" in i for i in issues)
    # the gap pick was NOT merged — a hole stays visible
    assert [p.number for p in state.picks] == [1]


def test_parse_draft_results_finds_nested_picks():
    payload = {"fantasy_content": {"league": [{"draft_results": {
        "0": {"draft_result": {"pick": "1", "team_key": "t.1", "player_key": "478.p.111"}},
        "1": {"draft_result": {"pick": "2", "team_key": "t.2", "player_key": "478.p.222"}},
    }}]}}
    picks = parse_draft_results(payload)
    assert [(p["pick"], p["player_id"]) for p in picks] == [(1, "111"), (2, "222")]


# --- output --------------------------------------------------------------------


def test_render_recommendation_shows_degrade_and_rows():
    from fantasy_gm.draft.live import Candidate, Recommendation

    rec = Recommendation(
        pick_number=3, on_the_clock=3, my_seat=3, mode="board", elapsed_s=0.2,
        degraded=True, note="degraded to static board (4.5)",
        candidates=[Candidate(player_id="1", name="Player One", board_rank=1,
                              total=9.5, value_over_safe=1.25, survival=0.4,
                              categories={"pts": 2.1, "ast": 1.0})],
    )
    out = render_recommendation(rec)
    assert "DEGRADED" in out and "4.5" in out
    assert "Player One" in out and "+1.25" in out and "40%" in out


# --- persistence ----------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path):
    state = DraftState(league_key="478.l.25733", n_teams=12, my_seat=7)
    state.add_pick("5", "Five", "manual")
    state.add_pick("6", "Six", "live")
    path = tmp_path / "state.json"
    save_state(state, path)
    loaded = load_state(path)
    assert loaded.league_key == state.league_key
    assert loaded.my_seat == 7
    assert [(p.number, p.player_id, p.source) for p in loaded.picks] == \
        [(1, "5", "manual"), (2, "6", "live")]


def test_build_player_directory_includes_incoming_players():
    from tests.test_adp import _store_with

    store = _store_with("Nikola Jokic")
    by_id, by_key = build_player_directory(store, "2026-27")
    assert by_id and "nikola jokic" in by_key
