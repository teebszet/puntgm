"""The draft-night surface: state mechanics, manual entry, reconciliation, output."""

from __future__ import annotations

import pytest

from fantasy_gm.draft.live import (
    COLUMN_SPECS,
    DEFAULT_COLUMNS,
    DraftState,
    Pick,
    _seat_of,
    apply_manual_pick,
    build_player_directory,
    load_state,
    normalize_name,
    parse_columns,
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


def test_my_next_pick_forward_snake_and_end():
    def with_picks(k, my_seat=1):
        state = DraftState(league_key="t", n_teams=12, my_seat=my_seat)
        for i in range(1, k + 1):
            state.picks.append(Pick(number=i, player_id=f"p{i}",
                                    team_seat=_seat_of(i, 12), source="manual"))
        return state
    # seat 1's picks: 1, 24, 25, 48, 49 ... (snake turns); last is #145
    assert with_picks(0).my_next_pick() == 1
    assert with_picks(4).my_next_pick() == 24       # 5-12 belong to seats 5-12
    assert with_picks(19).my_next_pick() == 24      # mid round 2, seat 5 on the clock
    assert with_picks(24).my_next_pick() == 25
    assert with_picks(144).my_next_pick() == 145    # seat 1 on the clock at 145
    assert with_picks(145).my_next_pick() is None   # seat 1 done; others finish 146-156
    # seat 5: picks 5 and 20
    assert with_picks(4, my_seat=5).my_next_pick() == 5
    assert with_picks(5, my_seat=5).my_next_pick() == 20


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


def test_reconcile_skips_mock_placeholder_slots():
    # Yahoo mock feeds pre-fill every remaining slot with the drawn draft order and no
    # player. Those rows are placeholders, not picks: no issue lines, no state changes.
    state = DraftState(league_key="t", n_teams=12, my_seat=1)
    platform = [
        {"pick": 20, "team_key": "478.l.2654071.t.5", "player_id": None},
        {"pick": 21, "team_key": "478.l.2654071.t.4", "player_id": None},
        {"pick": 156, "team_key": "478.l.2654071.t.12", "player_id": None},
    ]
    assert reconcile(state, platform, {}) == []
    assert state.picks == []


def test_reconcile_translates_foreign_ids_by_name():
    # Platform ids speak Yahoo; the board/store speak their own id space. The pick's
    # embedded name resolves through the directory's normalized-name index.
    state = DraftState(league_key="t", n_teams=12, my_seat=1)
    by_id = {"203999": "Nikola Jokic", "1628983": "Shai Gilgeous-Alexander"}
    by_key = {}
    for pid, name in by_id.items():
        by_key.setdefault(normalize_name(name), []).append(pid)
    platform = [
        {"pick": 1, "team_key": "t.1", "player_id": "5352", "name": "Nikola Jokić"},
        {"pick": 2, "team_key": "t.2", "player_id": "201566", "name": "Shai Gilgeous-Alexander"},
    ]
    issues = reconcile(state, platform, by_id, by_key)
    assert issues == []
    assert [p.player_id for p in state.picks] == ["203999", "1628983"]
    assert state.picks[0].name == "Nikola Jokic"


def test_reconcile_flags_ambiguous_name_without_guessing():
    state = DraftState(league_key="t", n_teams=4, my_seat=1)
    by_id = {"1": "Gary Payton", "2": "Gary Payton II"}
    by_key = {"gary payton": ["1", "2"]}
    platform = [{"pick": 1, "team_key": "t.1", "player_id": "77", "name": "Gary Payton"}]
    issues = reconcile(state, platform, by_id, by_key)
    assert any("matches several players" in i for i in issues)
    assert state.picks == []


def test_reconcile_records_unresolvable_and_keeps_clock():
    # A name our directory lacks must not stall the clock: record under the platform
    # id, flag it, and keep merging the picks after it.
    state = DraftState(league_key="t", n_teams=4, my_seat=1)
    by_id = {"203999": "Nikola Jokic"}
    by_key = {"nikola jokic": ["203999"]}
    platform = [
        {"pick": 1, "team_key": "t.1", "player_id": "777", "name": "Draft Nugget"},
        {"pick": 2, "team_key": "t.2", "player_id": "5352", "name": "Nikola Jokić"},
    ]
    issues = reconcile(state, platform, by_id, by_key)
    assert any("not in our directory" in i for i in issues)
    assert [p.player_id for p in state.picks] == ["777", "203999"]
    assert state.pick_number == 3


def test_parse_draft_results_extracts_embedded_name_and_placeholders():
    # Real shape (captured 2026-10-08 from a live mock): draft_result embeds the player
    # node with ;out=players; unfilled slots have a pick and team but no player_key.
    payload = {"fantasy_content": {"league": [{"draft_results": {
        "0": {"draft_result": {
            "pick": 1, "round": 1, "team_key": "478.l.2654071.t.1",
            "player_key": "478.p.5352",
            "0": {"players": {"0": {"player": [["478.p.5352"], {"player_id": "5352"},
                   {"name": {"full": "Nikola Jokić", "first": "Nikola", "last": "Jokić"}}]}}}}},
        "1": {"draft_result": {"pick": 20, "team_key": "478.l.2654071.t.5"}},
    }}]}}
    picks = parse_draft_results(payload)
    assert picks[0]["player_id"] == "5352"
    assert picks[0]["name"] == "Nikola Jokić"
    assert picks[1]["player_id"] is None and picks[1]["name"] is None


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


# --- the column model (4.2-4.4) --------------------------------------------------


def _candidate(**kw):
    from fantasy_gm.draft.live import Candidate

    base = dict(player_id="1", name="Player One", board_rank=1, total=9.5,
                value_over_safe=1.25, survival=0.4, categories={"pts": 2.1})
    base.update(kw)
    return Candidate(**base)


def test_parse_columns_default_subset_and_duplicates():
    assert parse_columns(None) == DEFAULT_COLUMNS
    assert parse_columns("surv, adp") == ("surv", "adp")
    assert parse_columns("adp,adp, surv") == ("adp", "surv")  # duplicates collapse
    with pytest.raises(ValueError, match="no columns requested"):
        parse_columns("")  # an explicitly empty spec is an error, not the default


def test_parse_columns_reports_unknown():
    with pytest.raises(ValueError, match="bogus_col"):
        parse_columns("rk,bogus_col")
    with pytest.raises(ValueError, match="nope1.*nope2|nope2.*nope1"):
        parse_columns("nope1,nope2")
    with pytest.raises(ValueError, match="no columns requested"):
        parse_columns("  ,  ")


def test_render_recommendation_explicit_absence_and_market_columns():
    from fantasy_gm.draft.live import Recommendation

    # board_rank 5 priced at adp 2: the market expects them three picks EARLIER than
    # the board does — adp_dev = adp - rank = -3.
    priced = _candidate(board_rank=5, adp=2, adp_dev=-3,
                        neg_cats=(("tov", -0.8), ("fg_pct", -0.3)))
    unpriced = _candidate(player_id="2", name="Player Two", board_rank=2, total=8.0,
                          value_over_safe=0.5, survival=0.6)
    rec = Recommendation(pick_number=1, on_the_clock=1, my_seat=1, mode="board",
                         elapsed_s=0.1, degraded=False, note="",
                         candidates=[priced, unpriced])

    # Explicit absence: a column set without the market columns renders neither.
    out = render_recommendation(rec, ("rk", "player", "value"))
    assert "adp" not in out and "Player One" in out
    # Default set: the adp column shows the priced pick number and explicit absence
    # (—) for the unpriced player, never a fake 0. Cells in column order per row:
    full = render_recommendation(rec, parse_columns(None))
    rows = [ln for ln in full.splitlines() if "Player" in ln]
    assert rows[0].split() == ["5", "Player", "One", "9.50", "+1.25", "40%",
                               "2", "pts+2.10", "tov-0.80", "fg-0.30"]
    assert rows[1].split() == ["2", "Player", "Two", "8.00", "+0.50", "60%",
                               "—", "pts+2.10"]  # no neg cats: nothing after top cats
    # The gap column on request: signed pick difference, — when unpriced.
    gap = render_recommendation(rec, ("rk", "player", "adp", "adp_dev"))
    grows = [ln for ln in gap.splitlines() if "Player" in ln]
    assert grows[0].split()[-2:] == ["2", "-3"] and grows[1].split()[-2:] == ["—", "—"]


def test_render_recommendation_neg_cats_only_when_genuinely_negative():
    from fantasy_gm.draft.live import Recommendation

    drags = _candidate(neg_cats=(("tov", -0.8),))
    clean = _candidate(player_id="2", name="Player Two", board_rank=2, total=8.0,
                       value_over_safe=0.5, survival=0.6, categories={"pts": 1.0})
    rec = Recommendation(pick_number=1, on_the_clock=1, my_seat=1, mode="board",
                         elapsed_s=0.1, degraded=False, note="",
                         candidates=[drags, clean])
    out = render_recommendation(rec, ("rk", "player", "neg_cats"))
    assert "tov" in out
    lines = [ln for ln in out.splitlines() if "Player" in ln]
    assert len(lines) == 2 and "tov" not in lines[1]  # a player who drags nothing: none


def test_candidate_json_additive_schema():
    from fantasy_gm.draft.live import candidate_json

    priced = candidate_json(_candidate(categories={"pts": 2.1, "tov": -0.8},
                                       adp=2, adp_dev=-3,
                                       neg_cats=(("tov", -0.8), ("fg_pct", -0.3))))
    unpriced = candidate_json(_candidate(player_id="2", name="Two", board_rank=2,
                                         total=8.0, value_over_safe=0.5, survival=0.6,
                                         categories={"reb": 1.0}))
    # The 4.1 keys the page already consumed are all still present (additive, R5).
    old_keys = {"name", "board_rank", "total", "value_over_safe", "survival",
                "categories", "engine_value"}
    assert old_keys <= set(priced)
    # Market columns: ints when priced, explicit nulls when not; neg cats worst first.
    assert (priced["adp"], priced["adp_dev"]) == (2, -3)
    assert (unpriced["adp"], unpriced["adp_dev"]) == (None, None)
    assert priced["neg_cats"] == [["tov", -0.8], ["fg_pct", -0.3]]
    assert unpriced["neg_cats"] == []


def test_column_spec_json_is_the_page_menu():
    from fantasy_gm.draft.live import column_spec_json

    menu = column_spec_json()
    assert [s["key"] for s in menu] == [s.key for s in COLUMN_SPECS]
    assert [s["key"] for s in menu if s["default"]] == list(DEFAULT_COLUMNS)
    assert all(set(s) == {"key", "header", "default"} for s in menu)


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
