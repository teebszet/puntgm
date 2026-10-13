"""The per-pick display panels: the differential (R6/D5) and the punt panel (R7/R8/D6).

The panels are display: the static board's ordering stands, and the tests pin that the
panels never reorder it and never mutate the draft state. The punt panel's per-build
totals are the same sums ``build_board(punt=...)`` ranks on — pinned by test against a
real board, not asserted (panels.py's claim).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fantasy_gm.config import DEFAULT_CATEGORIES
from fantasy_gm.data.store import Store
from fantasy_gm.draft.board import (
    PUNT_BUILDS,
    AvailabilityMode,
    build_board,
)
from fantasy_gm.draft.live import DraftState, Pick
from fantasy_gm.draft.panels import (
    PuntPanelCache,
    compute_differential,
    compute_punt_panel,
    differential_json,
    parse_punt,
    punt_panel_json,
    render_differential,
    render_punt_panel,
)
from fantasy_gm.models import Game, PlayerGameLog, UsageRole

SEASON = "2025-26"
START = date(2025, 10, 20)  # a Monday


def _line(**c):
    base = {k: 0.0 for k in ("pts", "reb", "ast", "stl", "blk", "fg3m", "tov",
                             "fgm", "fga", "ftm", "fta")}
    base.update(c)
    return base


def _seed(store: Store, players: dict[str, list[dict | None]], minutes: float = 30.0):
    n_days = max(len(v) for v in players.values())
    for day_i in range(n_days):
        d = (START + timedelta(days=day_i)).isoformat()
        store.upsert_games([Game(f"g{day_i}", SEASON, d, "AAA", "BBB")])
        for pid, lines in players.items():
            if day_i < len(lines) and lines[day_i] is not None:
                store.upsert_player_logs(
                    [PlayerGameLog(f"g{day_i}", SEASON, d, pid, pid, "AAA", lines[day_i])]
                )
                store.add_usage_role([UsageRole(pid, d, minutes, 12.0, True, 1)])


def _pool_store() -> Store:
    """A pool with shape: a scorer, a rebounder, a turnover-prone scorer, and filler."""
    store = Store(":memory:")
    _seed(store, {
        "scorer": [_line(pts=30, tov=1) for _ in range(28)],
        "boards": [_line(pts=10, reb=14) for _ in range(28)],
        "sloppy": [_line(pts=28, tov=8) for _ in range(28)],
        "filler": [_line(pts=8, reb=3) for _ in range(28)],
    })
    return store


def _basis(pool_size: int = 4):
    capture: dict = {}
    board = build_board(_pool_store(), SEASON, pool_size=pool_size,
                        availability=AvailabilityMode.NEUTRAL, capture=capture)
    return board, capture["basis"]


def _state(n_teams: int = 12, my_seat: int = 1) -> DraftState:
    return DraftState(league_key="t", n_teams=n_teams, my_seat=my_seat)


def _give(state: DraftState, seat: int, pids: list[str]) -> None:
    """Record one pick per pid for ``seat`` at the current pick number."""
    for pid in pids:
        state.picks.append(Pick(number=state.pick_number, player_id=pid, team_seat=seat,
                                source="manual"))


# --- parse_punt (R7: "e.g. ft%,pts") ----------------------------------------------


def test_parse_punt_accepts_the_manager_spelling():
    assert parse_punt("ft%,pts") == ("ft_pct", "pts")
    assert parse_punt("FT_PCT, tov") == ("ft_pct", "tov")
    assert parse_punt("threes;3pm") == ("fg3m",)


def test_parse_punt_rejects_unknown_and_names_the_known_set():
    with pytest.raises(ValueError, match="unknown category") as exc:
        parse_punt("ft%,hustle")
    assert "TOV" in str(exc.value)  # the known set is in the error (display labels)


def test_parse_punt_collapses_duplicates_and_rejects_a_full_punt():
    assert parse_punt("pts,pts,tov") == ("pts", "tov")
    with pytest.raises(ValueError, match="cannot punt every category"):
        parse_punt(",".join(DEFAULT_CATEGORIES))
    with pytest.raises(ValueError, match="no categories"):
        parse_punt(" , ")


# --- the differential (R6/D5) ------------------------------------------------------


def test_differential_field_is_the_other_teams_and_strongest_is_highest_total():
    board, basis = _basis()
    state = _state(n_teams=4, my_seat=1)
    _give(state, 1, ["scorer"])
    _give(state, 2, ["boards", "sloppy"])
    _give(state, 3, ["filler"])
    d = compute_differential(state, basis)
    assert d.my_seat == 1
    # The average is over the other three rosters, never ours.
    opp = [state.roster_of(s) for s in (2, 3, 4)]
    for c in basis.categories:
        per_roster = []
        for roster in opp:
            per_roster.append(sum(basis.stats[p][c].mean for p in roster if p in basis.stats))
        assert d.field_avg[c] == pytest.approx(sum(per_roster) / 3)
    # Strongest = the opposing roster with the highest summed G-score.
    totals = {s: sum(basis.total(p) for p in state.roster_of(s) if p in basis.stats)
              for s in (2, 3, 4)}
    assert d.strongest_seat == max(totals, key=lambda s: totals[s])


def test_differential_without_picks_has_no_strongest_roster():
    _, basis = _basis()
    d = compute_differential(_state(), basis)
    assert d.strongest_seat is None
    assert all(v == 0.0 for v in d.field_avg.values())
    assert all(v == 0.0 for v in d.our.values())


def test_differential_counts_unpriced_rostered_players():
    _, basis = _basis()
    state = _state(n_teams=4, my_seat=1)
    _give(state, 1, ["scorer", "ghost"])
    d = compute_differential(state, basis)
    assert d.unpriced_rostered == 1


def test_differential_speaks_category_direction_for_tov():
    """``tov`` lower is better: more turnovers than the field is BEHIND, not ahead."""
    board, basis = _basis()
    state = _state(n_teams=4, my_seat=1)
    _give(state, 1, ["sloppy"])   # 8 tov/game
    _give(state, 2, ["scorer"])   # 1 tov/game
    d = compute_differential(state, basis)
    tov = next(c for c in differential_json(d)["categories"] if c["cat"] == "tov")
    pts = next(c for c in differential_json(d)["categories"] if c["cat"] == "pts")
    assert tov["standing"] == "behind"
    assert pts["standing"] == "ahead"


def test_panels_never_touch_the_board_or_the_state():
    """Display-only (R6): candidate ordering still comes from the static board."""
    board, basis = _basis()
    before_rows = [(r.player_id, r.total, r.rank) for r in board.rows]
    state = _state(n_teams=4, my_seat=1)
    _give(state, 1, ["scorer"])
    _give(state, 2, ["filler"])
    before_picks = [(p.number, p.player_id, p.team_seat) for p in state.picks]

    compute_differential(state, basis)
    compute_punt_panel(state, basis, adp_order=None, names={})

    assert [(r.player_id, r.total, r.rank) for r in board.rows] == before_rows
    assert [(p.number, p.player_id, p.team_seat) for p in state.picks] == before_picks


# --- the punt panel (R7/R8/D6) -----------------------------------------------------


def test_punt_panel_excludes_drafted_players_and_conditions_on_state():
    board, basis = _basis()
    state = _state(n_teams=4, my_seat=1)
    _give(state, 1, ["scorer"])
    _give(state, 2, ["boards"])
    panel = compute_punt_panel(state, basis, adp_order=None,
                               names={"scorer": "Scorer", "filler": "Filler"})
    for b in panel.builds:
        ranked_ids = {c.player_id for c in b.rows}
        assert "scorer" not in ranked_ids and "boards" not in ranked_ids
        assert {"sloppy", "filler"} <= ranked_ids  # the remaining pool is what is ranked


def test_punt_panel_accepts_a_custom_set():
    board, basis = _basis()
    panel = compute_punt_panel(_state(), basis, adp_order=None, names={},
                               custom=parse_punt("ft%,pts"))
    custom = next(b for b in panel.builds if b.build == "custom")
    assert custom.punt == ("ft_pct", "pts")
    assert set(custom.scored) == set(DEFAULT_CATEGORIES) - {"ft_pct", "pts"}
    assert any(b.build == "balanced" for b in panel.builds)  # the named builds all ran too


def test_punt_panel_totals_are_the_same_sums_build_board_ranks_on():
    """The equivalence panels.py claims: a build's total == build_board(punt=...)'s total."""
    store = _pool_store()
    capture: dict = {}
    full_board = build_board(store, SEASON, pool_size=4, availability=AvailabilityMode.NEUTRAL,
                             capture=capture)
    basis = capture["basis"]
    pool = [r.player_id for r in full_board.rows]
    state = _state(n_teams=4, my_seat=1)  # nothing drafted: every pool player available
    for build in ("punt_tov", "punt_fg_ft", "balanced"):
        panel = compute_punt_panel(state, basis, adp_order=None, names={},
                                   custom=(), top_n=len(pool)) if build == "balanced" else None
        if panel is None:
            panel = compute_punt_panel(state, basis, adp_order=None, names={},
                                       custom=("tov",) if build == "punt_tov" else
                                       ("fg_pct", "ft_pct"), top_n=len(pool))
        view = next(b for b in panel.builds
                    if b.build in (build, "custom") and
                    tuple(b.punt) == PUNT_BUILDS.get(build, ("tov",) if build == "punt_tov"
                                                     else ("fg_pct", "ft_pct")))
        ref = build_board(store, SEASON, PUNT_BUILDS.get(build, view.punt), pool_size=4,
                          availability=AvailabilityMode.NEUTRAL)
        board_totals = {r.player_id: r.total for r in ref.rows}
        for row in view.rows:
            assert row.total == pytest.approx(board_totals[row.player_id]), build


def test_punt_panel_cache_hits_within_a_pick_and_invalidates_on_the_next():
    board, basis = _basis()
    cache = PuntPanelCache()
    state = _state(n_teams=4, my_seat=1)
    p1 = cache.get(state, basis, None, {})
    assert cache.get(state, basis, None, {}) is p1          # same pick, same custom: hit
    _give(state, 2, ["filler"])
    p2 = cache.get(state, basis, None, {})
    assert p2 is not p1 and p2.pick_number == state.pick_number  # next pick: recompute


def test_punt_panel_cache_respects_a_changed_custom_set():
    board, basis = _basis()
    cache = PuntPanelCache()
    state = _state(n_teams=4, my_seat=1)
    a = cache.get(state, basis, None, {})
    b = cache.get(state, basis, None, {}, custom=("tov",))
    assert b is not a and any(x.build == "custom" for x in b.builds)


def test_punt_panel_degrades_to_the_last_complete_set_marked_stale():
    board, basis = _basis()
    cache = PuntPanelCache()
    state = _state(n_teams=4, my_seat=1)
    good = cache.get(state, basis, None, {})
    assert good.complete and not good.stale

    _give(state, 2, ["filler"])                       # the clock moves
    overrun = cache.get(state, basis, None, {}, budget_s=0.0)
    # The last complete panel is shown as-is — a copy marked stale, with ITS pick number.
    assert overrun.stale and overrun.pick_number == 1
    assert overrun.current_pick == state.pick_number   # ...and "now" on the side.
    assert "pick 1" in overrun.note

    # A fresh cache with no complete panel never dresses an incomplete set as current.
    fresh = PuntPanelCache()
    never = fresh.get(state, basis, None, {}, budget_s=0.0)
    assert not never.complete and not never.stale and never.current_pick == never.pick_number


def test_first_overrun_reports_the_budget_without_fabricating_builds():
    _, basis = _basis()
    panel = compute_punt_panel(_state(), basis, None, {}, budget_s=0.0)
    assert not panel.complete and panel.builds == []
    assert "budget" in panel.note


# --- json + terminal render --------------------------------------------------------


def test_json_and_render_are_additive_and_labeled():
    board, basis = _basis()
    state = _state(n_teams=4, my_seat=1)
    _give(state, 1, ["scorer"])
    _give(state, 2, ["boards", "sloppy"])
    d = compute_differential(state, basis)
    dj = differential_json(d)
    assert set(dj) == {"pick_number", "my_seat", "strongest_seat", "unpriced_rostered",
                       "categories"}
    assert all(set(c) == {"cat", "label", "our", "avg", "strong", "standing"}
               for c in dj["categories"])

    panel = compute_punt_panel(state, basis, adp_order=None, names={})
    pj = punt_panel_json(panel)
    assert set(pj) == {"pick_number", "current_pick", "stale", "complete", "note",
                       "elapsed_s", "builds"}
    assert all(set(b) == {"build", "label", "punt", "scored", "outlook", "rows"}
               for b in pj["builds"])

    txt = render_differential(d)
    assert "context" in txt and "display only" in txt
    pt = render_punt_panel(panel, detail="balanced")
    assert "context" in pt and "display only" in pt


def test_capture_hands_the_fitted_basis_out():
    capture: dict = {}
    build_board(_pool_store(), SEASON, pool_size=4, availability=AvailabilityMode.NEUTRAL,
                capture=capture)
    basis = capture["basis"]
    assert set(basis.categories) == set(DEFAULT_CATEGORIES)
    assert set(basis.stats) <= set(basis.pool)
