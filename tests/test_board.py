"""The static G-score board: punt builds, and the availability/variance separation.

The availability tests carry most of the weight here. The first board this module produced
disagreed with z-score mainly because it counted missed weeks as zeros, which is correct when
grading a season that already happened and hindsight when ranking one that has not. These
tests pin the three treatments apart so that distinction cannot quietly collapse again.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from fantasy_gm.data.store import Store
from fantasy_gm.draft.board import (
    PUNT_BUILDS,
    AvailabilityMode,
    Board,
    RateBasis,
    all_builds,
    biggest_movers,
    board_json,
    build_board,
    export,
    project_availability,
    render_markdown,
    render_table,
)
from fantasy_gm.draft.xscore import PeriodStats
from fantasy_gm.models import Availability, ForwardRoster, Game, PlayerGameLog, UsageRole

SEASON = "2025-26"
START = date(2025, 10, 20)  # a Monday
AS_OF = "2025-10-19"        # the day before: no part of SEASON is visible to a fit


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


# --- punt builds -------------------------------------------------------------


def test_punting_a_category_removes_it_from_the_scored_set():
    board = build_board(_pool_store(), SEASON, punt=("tov",), pool_size=4,
                        availability=AvailabilityMode.NEUTRAL)
    assert "tov" not in board.categories
    assert board.punt == ("tov",)
    assert all("tov" not in r.categories for r in board.rows)


def test_punting_turnovers_promotes_the_turnover_prone_player():
    """The point of a punt build: the category you conceded stops costing you."""
    store = _pool_store()
    full = build_board(store, SEASON, pool_size=4, availability=AvailabilityMode.NEUTRAL)
    punted = build_board(store, SEASON, punt=("tov",), pool_size=4,
                         availability=AvailabilityMode.NEUTRAL)
    rank = lambda b, p: next(r.rank for r in b.rows if r.player_id == p)  # noqa: E731
    assert rank(punted, "sloppy") < rank(full, "sloppy")


def test_unknown_punt_category_is_rejected():
    with pytest.raises(ValueError, match="not 9-cat categories"):
        build_board(_pool_store(), SEASON, punt=("hustle",), pool_size=4,
                    availability=AvailabilityMode.NEUTRAL)


def test_cannot_punt_every_category():
    from fantasy_gm.config import DEFAULT_CATEGORIES

    with pytest.raises(ValueError, match="cannot punt every category"):
        build_board(_pool_store(), SEASON, punt=tuple(DEFAULT_CATEGORIES), pool_size=4,
                    availability=AvailabilityMode.NEUTRAL)


def test_all_builds_covers_the_named_set():
    boards = all_builds(_pool_store(), SEASON, pool_size=4,
                        availability=AvailabilityMode.NEUTRAL)
    assert {b.build for b in boards} == set(PUNT_BUILDS)


# --- availability: the separation this module exists to make -----------------


def _durable_vs_injured() -> Store:
    """Identical per-game production; one player misses a stretch in the middle and returns.

    The absence is deliberately *interior*. ``measure_period_stats`` counts idle weeks only
    inside a player's active span, so a season-ending injury is invisible to the realized
    treatment while an identical mid-season one is fully charged — see
    :func:`test_realized_treatment_cannot_see_a_season_ending_absence`.
    """
    store = Store(":memory:")
    _seed(store, {
        "durable": [_line(pts=25) for _ in range(28)],
        "injured": [_line(pts=25) if (i < 7 or i >= 21) else None for i in range(28)],
        "filler": [_line(pts=6) for _ in range(28)],
    })
    return store


def test_realized_availability_penalises_missed_weeks():
    """Idle weeks as zeros — correct for replay, and the reason the two players separate."""
    board = build_board(_durable_vs_injured(), SEASON, pool_size=3,
                        availability=AvailabilityMode.REALIZED)
    scores = {r.player_id: r.total for r in board.rows}
    assert scores["durable"] > scores["injured"]


def test_realized_treatment_cannot_see_a_season_ending_absence():
    """A known asymmetry, pinned so it is not mistaken for a bug later.

    Idle weeks are counted only *within* a player's observed span, so a player who goes down in
    February and never returns is scored on their healthy weeks alone, while an identical
    player who misses the same number of weeks mid-season is charged for all of them. The
    realized arm therefore *understates* the availability effect rather than overstating it,
    which matters when reading how much of the board's edge over z-score is availability.
    """
    store = Store(":memory:")
    _seed(store, {
        "durable": [_line(pts=25) for _ in range(28)],
        "ended": [_line(pts=25) if i < 14 else None for i in range(28)],
        "filler": [_line(pts=6) for _ in range(28)],
    })
    board = build_board(store, SEASON, pool_size=3, availability=AvailabilityMode.REALIZED)
    scores = {r.player_id: r.total for r in board.rows}
    assert scores["durable"] == pytest.approx(scores["ended"], abs=1e-9)


def test_neutral_availability_ignores_missed_weeks():
    """Active weeks only: identical per-game production ranks identically, by construction.

    This is what isolates the variance claim from the availability claim — and it is also why
    `neutral` is an ablation rather than the product, since it rates a half-season player as
    if durable.
    """
    board = build_board(_durable_vs_injured(), SEASON, pool_size=3,
                        availability=AvailabilityMode.NEUTRAL)
    scores = {r.player_id: r.total for r in board.rows}
    assert scores["durable"] == pytest.approx(scores["injured"], abs=1e-9)


def test_projected_availability_needs_an_as_of():
    with pytest.raises(ValueError, match="needs an --as-of"):
        build_board(_pool_store(), SEASON, pool_size=4,
                    availability=AvailabilityMode.PROJECTED)


def test_player_with_no_prior_history_gets_the_pool_rate_not_certainty():
    """Regression: a rookie is not an 82-game lock.

    ``project_availability`` reads games *before* ``as_of``. A player with none was previously
    absent from the result and so fell through to a rate of 1.0 — which put two rookies in the
    top eight of the first real board. They must instead take the fitted pool rate.
    """
    store = _pool_store()
    projections = project_availability(store, SEASON, AS_OF, players=["rookie", "scorer"])
    assert "rookie" in projections
    assert projections["rookie"].availability_rate < 1.0
    assert projections["rookie"].observed_games == 0


def test_weekly_totals_are_compounded_binomially_over_games_not_bernoulli_over_weeks():
    """The game count in ``tau'2 = n·r·v + n·r(1−r)·m2`` is load-bearing.

    Treating availability as a coin flip on the *week* drops the ``n``, inflating the penalty
    by games-per-week (~3.3x), and because the term scales with ``m2`` the error lands almost
    entirely on high-production players — it ranked durable role players above every star. Pin
    the exact identity rather than the symptom.
    """
    from fantasy_gm.draft.board import compound_weekly

    per_game = {"p": {"pts": PeriodStats(mean=25.0, std=10.0, periods=40)}}
    r, n = 0.8, 4.0
    out = compound_weekly(per_game, {"p": r}, n)["p"]["pts"]

    assert out.mean == pytest.approx(n * r * 25.0)
    assert out.std == pytest.approx((n * r * 100.0 + n * r * (1 - r) * 625.0) ** 0.5)
    # The Bernoulli-over-weeks form applies the same rate to a whole week's production.
    bernoulli = (r * (n * 100.0) + r * (1 - r) * (n * 25.0) ** 2) ** 0.5
    assert out.std < bernoulli


def test_full_availability_is_the_plain_sum_of_n_games():
    """r = 1 must recover an unweighted week: n games' mean and n games' variance."""
    from fantasy_gm.draft.board import compound_weekly

    per_game = {"p": {"pts": PeriodStats(mean=25.0, std=10.0, periods=40)}}
    out = compound_weekly(per_game, {"p": 1.0}, 4.0)["p"]["pts"]
    assert out.mean == pytest.approx(100.0)
    assert out.std == pytest.approx((4.0 * 100.0) ** 0.5)


def test_a_forward_board_ignores_realized_games_per_week():
    """The second hindsight leak (A-DRAFT-14), pinned.

    Two players with identical per-game production and identical availability projections must
    tie on a forward board even when one of them actually played far more often. Building the
    board from weekly totals over active weeks failed this: the busier player's weeks were
    fuller, so he ranked higher on information from the season being graded.
    """
    store = Store(":memory:")
    _seed(store, {
        "busy": [_line(pts=20, reb=5) for _ in range(40)],
        "rested": [_line(pts=20, reb=5) if i % 3 == 0 else None for i in range(40)],
        "filler": [_line(pts=6, reb=2) for _ in range(40)],
    })
    board = build_board(store, SEASON, pool_size=3, availability=AvailabilityMode.NEUTRAL)
    rows = {r.player_id: r.total for r in board.rows}
    assert rows["busy"] == pytest.approx(rows["rested"])


# --- provenance and rendering ------------------------------------------------


@pytest.mark.parametrize(
    "mode,needle",
    [
        (AvailabilityMode.REALIZED, "hindsight"),
        (AvailabilityMode.NEUTRAL, "Ablation"),
    ],
)
def test_basis_line_states_the_availability_treatment(mode, needle):
    board = build_board(_pool_store(), SEASON, pool_size=4, availability=mode)
    assert needle in board.basis


def test_projected_basis_line_names_the_as_of_date():
    """A published board has to say what date its availability projection was made from,
    or a reader cannot tell whether it saw the season it ranks."""
    board = build_board(_pool_store(), SEASON, pool_size=4,
                        availability=AvailabilityMode.PROJECTED, as_of=AS_OF)
    assert AS_OF in board.basis
    assert AS_OF in board_json(board)["basis"]
    assert AS_OF in render_markdown(board)


def test_z_delta_is_positive_when_z_score_ranks_the_player_worse():
    board = build_board(_pool_store(), SEASON, pool_size=4,
                        availability=AvailabilityMode.NEUTRAL)
    for r in board.rows:
        if r.z_rank is not None:
            assert r.z_delta == r.z_rank - r.rank


def test_biggest_movers_ignores_rows_without_a_z_rank():
    board = build_board(_pool_store(), SEASON, pool_size=4,
                        availability=AvailabilityMode.NEUTRAL, with_zscore=False)
    under, over = biggest_movers(board)
    assert under == [] and over == []


def test_export_writes_a_manifest_and_one_pair_per_build(tmp_path):
    boards = all_builds(_pool_store(), SEASON, pool_size=4,
                        availability=AvailabilityMode.NEUTRAL)
    export(boards, tmp_path)
    manifest = json.loads((tmp_path / "index.json").read_text())
    assert {b["build"] for b in manifest["builds"]} == set(PUNT_BUILDS)
    assert manifest["basis"]
    for b in boards:
        assert (tmp_path / f"{b.build}.json").exists()
        assert (tmp_path / f"{b.build}.md").exists()


def test_empty_board_renders_without_raising():
    board = Board(season=SEASON, build="balanced", punt=(), categories=("pts",),
                  pool_size=0, kappa=1.0, variance_mode="measured")
    assert board.basis
    assert render_markdown(board)


def test_a_published_board_applies_no_variance_penalty_by_default():
    """κ=0 is measured, not incidental (A-DRAFT-4).

    Swept over {0, 0.25, 0.5, 1, 2, 4} in a seat-mirrored replay across two seasons and four
    seeds, in both forward-honest and hindsight pairings, κ=0 won every run and the decline was
    monotone. The variance correction — the entire thesis of G-score — is harmful on this data.
    Anyone raising this default is reversing a measurement and should have to edit a test.
    """
    from fantasy_gm.draft.board import BOARD_KAPPA

    assert BOARD_KAPPA == 0.0
    store = _pool_store()
    default = build_board(store, SEASON, pool_size=4, availability=AvailabilityMode.NEUTRAL)
    explicit = build_board(store, SEASON, pool_size=4, kappa=0.0,
                           availability=AvailabilityMode.NEUTRAL)
    assert [r.player_id for r in default.rows] == [r.player_id for r in explicit.rows]


def test_the_engines_kappa_is_left_alone():
    """The board's κ and the H₀ engine's are now deliberately different.

    H₀ scores through a Poisson-binomial over category wins, where the variance term does
    different work, and task 3.8 has not yet established that the implementation reproduces the
    paper at all. Moving both together would confound the two investigations.
    """
    from fantasy_gm.draft.board import BOARD_KAPPA
    from fantasy_gm.draft.xscore import DEFAULT_KAPPA

    assert DEFAULT_KAPPA == 1.0
    assert BOARD_KAPPA != DEFAULT_KAPPA


# --- R1/D1: the DNP split, and the provenance that names it --------------------


def test_dnp_rows_do_not_inflate_projected_availability():
    """A DNP row is a game NOT played: observed games and the availability rate the
    beta-binomial projects must be identical with and without DNP rows (R1)."""
    store = Store(":memory:")
    _seed(store, {"plain": [_line(pts=10) for _ in range(10)]})
    _seed(store, {"withdnp": [_line(pts=10) for _ in range(10)]})
    n_days = 10
    for day_i in range(n_days, n_days + 5):
        d = (START + timedelta(days=day_i)).isoformat()
        store.upsert_games([Game(f"dnp{day_i}", SEASON, d, "AAA", "BBB")])
        store.upsert_player_logs(
            [PlayerGameLog(f"dnp{day_i}", SEASON, d, "withdnp", "withdnp", "AAA", _line())]
        )
        store.add_usage_role([UsageRole("withdnp", d, 0.0, 0.0, False, 5)])

    projs = project_availability(store, SEASON, "2025-12-31")
    assert projs["plain"].observed_games == 10
    assert projs["withdnp"].observed_games == 10  # the 5 DNP rows count as games NOT played
    assert projs["withdnp"].expected_games == pytest.approx(projs["plain"].expected_games)


def test_basis_line_states_the_dnp_rule():
    """The provenance line must say rates are over games played and DNPs ride the
    availability term — a reader cannot audit a basis that does not state its rule (1.3)."""
    board = build_board(_pool_store(), SEASON, pool_size=4,
                        availability=AvailabilityMode.PROJECTED, as_of=AS_OF)
    assert "games actually played" in board.basis
    assert "DNP" in board.basis
    assert board_json(board)["basis"] == board.basis


def test_basis_line_names_forward_roster_placement():
    """When the pool places unsampled players by projected-roster depth, the line says so —
    derived placement is provenance, not something to guess at (R2)."""
    board = build_board(_pool_store(), SEASON, pool_size=4,
                        availability=AvailabilityMode.NEUTRAL, forward_season="2026-27")
    assert "2026-27" in board.basis
    assert board_json(board)["forward_season"] == "2026-27"


# --- R3: platform status caps the availability rate; baseline rates ------------


def _baseline_store() -> Store:
    """Ranked season with priced players, a returnee with zero 2025-26 logs but a healthy
    2024-25 sample, and a newcomer no season can price."""
    store = _pool_store()
    for gi in range(20):
        d = (date(2024, 11, 1) + timedelta(days=gi)).isoformat()
        store.upsert_games([Game(f"o{gi}", "2024-25", d, "AAA", "BBB")])
        store.upsert_player_logs([
            PlayerGameLog(f"o{gi}", "2024-25", d, "returnee", "Returnee", "AAA",
                          _line(pts=22, reb=4, ast=8))])
        store.add_usage_role([UsageRole("returnee", d, 34.0, 16.0, True, 1)])
    store.add_forward_roster([
        ForwardRoster("returnee", "2026-27", "IND", 2, known_from="2026-08-17"),
        ForwardRoster("newcomer", "2026-27", "SAS", 3, known_from="2026-08-17"),
    ])
    return store


def test_a_lost_season_is_priced_from_the_last_healthy_one_and_named_on_the_row():
    board = build_board(_baseline_store(), SEASON, pool_size=6,
                        availability=AvailabilityMode.PROJECTED, as_of=AS_OF,
                        forward_season="2026-27")
    row = next(r for r in board.rows if r.player_id == "returnee")
    assert row.rate_source == "baseline:2024-25"
    assert row.categories["pts"] > 0  # priced from the healthy season, not zeros
    assert "1 of those priced per-game from their last healthy season" in board.basis
    assert "baseline:2024-25" in board_json(board)["basis"] or \
        "last healthy season" in board_json(board)["basis"]
    assert board_json(board)["rows"][
        [r["player_id"] for r in board_json(board)["rows"]].index("returnee")
    ]["rate_source"] == "baseline:2024-25"


def test_a_player_nothing_can_price_is_reported_not_dropped():
    board = build_board(_baseline_store(), SEASON, pool_size=6,
                        availability=AvailabilityMode.PROJECTED, as_of=AS_OF,
                        forward_season="2026-27")
    assert [pid for pid, _name, _reason in board.unpriced] == ["newcomer"]
    assert "no usable 2025-26 sample" in board.unpriced[0][2]
    assert all(r.player_id != "newcomer" for r in board.rows)  # not ranked
    assert "unpriced (1)" in render_table(board)  # but never silently vanished


def test_season_ending_out_zeroes_expected_games_and_names_the_note():
    store = _pool_store()
    store.add_availability([Availability(
        "scorer", "OUT", "2025-10-15", "yahoo", 1.0, "torn achilles — out for season")])
    board = build_board(store, SEASON, pool_size=4,
                        availability=AvailabilityMode.PROJECTED, as_of=AS_OF)
    row = next(r for r in board.rows if r.player_id == "scorer")
    assert row.status == "OUT"
    assert row.status_note == "torn achilles — out for season"
    assert row.availability_rate == 0.0
    assert row.expected_games == 0.0
    assert board.status_as_of == AS_OF
    assert board.status_counts == {"OUT": 1}
    assert f"Platform status (yahoo, as of {AS_OF})" in board.basis
    assert "OUT 1" in board.basis
    exported = board_json(board)
    assert exported["status_as_of"] == AS_OF
    assert next(r for r in exported["rows"] if r["player_id"] == "scorer")["status_note"]


def test_short_term_out_halves_the_measured_rate():
    store = _pool_store()
    plain = build_board(store, SEASON, pool_size=4,
                        availability=AvailabilityMode.PROJECTED, as_of=AS_OF)
    plain_rate = next(r for r in plain.rows if r.player_id == "scorer").availability_rate
    store.add_availability([Availability(
        "scorer", "OUT", "2025-10-15", "yahoo", 0.9, "sprained ankle — out 2-3 weeks")])
    board = build_board(store, SEASON, pool_size=4,
                        availability=AvailabilityMode.PROJECTED, as_of=AS_OF)
    row = next(r for r in board.rows if r.player_id == "scorer")
    assert row.availability_rate == pytest.approx(plain_rate * 0.5)
    assert row.expected_games == pytest.approx(
        next(r for r in plain.rows if r.player_id == "scorer").expected_games * 0.5)


def test_questionable_shaves_a_quarter_and_active_never_raises():
    store = _pool_store()
    plain = build_board(store, SEASON, pool_size=4,
                        availability=AvailabilityMode.PROJECTED, as_of=AS_OF)
    plain_rate = next(r for r in plain.rows if r.player_id == "scorer").availability_rate
    store.add_availability([Availability(
        "scorer", "QUESTIONABLE", "2025-10-15", "yahoo", 0.9, "game-time decision")])
    board = build_board(store, SEASON, pool_size=4,
                        availability=AvailabilityMode.PROJECTED, as_of=AS_OF)
    row = next(r for r in board.rows if r.player_id == "scorer")
    assert row.availability_rate == pytest.approx(plain_rate * 0.75)

    # a designation the platform marks healthy must not price the player above measured
    store.add_availability([Availability(
        "boards", "ACTIVE", "2025-10-15", "yahoo", 0.9, "cleared to play")])
    raised = build_board(store, SEASON, pool_size=4,
                         availability=AvailabilityMode.PROJECTED, as_of=AS_OF)
    assert next(r for r in raised.rows if r.player_id == "boards").availability_rate \
        == pytest.approx(next(
            r for r in plain.rows if r.player_id == "boards").availability_rate)


def test_neutral_mode_does_not_consume_status():
    """Neutral is the no-availability ablation: showing an OUT badge while ranking the
    player as if durable would be the worst of both. Status rides the projected mode only."""
    store = _pool_store()
    store.add_availability([Availability(
        "scorer", "OUT", "2025-10-15", "yahoo", 1.0, "out for season")])
    board = build_board(store, SEASON, pool_size=4, availability=AvailabilityMode.NEUTRAL)
    assert all(r.status is None for r in board.rows)
    assert board.status_as_of is None
    assert board.status_counts == {}


# --- R4: the projected rate basis ---------------------------------------------


def _projected_store() -> Store:
    """The pool store plus a bench player whom the 2026-27 depth chart promotes into a lead
    role on a new team — a move the ranked season cannot see and the derived model prices
    (R4/D3). His ranked-season minutes wobble so the model's fits have spread to learn from:
    with every role constant the role-weight arithmetic degenerates, the same way it would in
    a league where nobody ever sat a night.
    """
    import random

    store = _pool_store()
    rng = random.Random(7)
    for day_i in range(28):
        d = (START + timedelta(days=day_i)).isoformat()
        store.upsert_player_logs([PlayerGameLog(
            f"g{day_i}", SEASON, d, "bench", "Bench", "AAA",
            _line(pts=5, reb=2, ast=1, stl=0.3, blk=0.2, fg3m=0.5, tov=0.8,
                  fgm=1.8, fga=4.0, ftm=1.0, fta=1.4))])
        store.add_usage_role([UsageRole(
            "bench", d, round(max(12.0 + rng.gauss(0, 2.0), 4.0), 1), 5.4, False, 8)])
    store.add_forward_roster(
        [ForwardRoster("bench", "2026-27", "CCC", 1, known_from="2026-08-17")])
    return store


def test_the_default_basis_is_measured_and_builds_todays_board():
    """No flag must change nothing: same pool, same totals, same provenance line (R4)."""
    store = _pool_store()
    plain = build_board(store, SEASON, pool_size=4, as_of=AS_OF)
    flagged = build_board(store, SEASON, pool_size=4, as_of=AS_OF, rate_basis="measured")
    assert [r.player_id for r in plain.rows] == [r.player_id for r in flagged.rows]
    assert [r.total for r in plain.rows] == [r.total for r in flagged.rows]
    assert plain.basis == flagged.basis
    assert plain.rate_basis == RateBasis.MEASURED
    assert plain.projected_as_of is None
    assert board_json(plain)["rate_basis"] == "measured"
    assert board_json(plain)["projected_as_of"] is None
    assert "Category rates are measured, not projected forward." in plain.basis


def test_projected_basis_moves_a_player_whose_forward_role_changed():
    """The point of the projected basis: the mover's rates follow his 2026-27 depth chart —
    production the measured basis cannot see (R4/D3)."""
    store = _projected_store()
    measured = build_board(store, SEASON, pool_size=5, availability=AvailabilityMode.NEUTRAL,
                           forward_season="2026-27")
    projected = build_board(store, SEASON, pool_size=5, availability=AvailabilityMode.NEUTRAL,
                            forward_season="2026-27",
                            rate_basis="projected", as_of="2026-10-16")
    was = next(r for r in measured.rows if r.player_id == "bench")
    now = next(r for r in projected.rows if r.player_id == "bench")
    assert was.rate_source is None  # measured rates come from the ranked season
    assert now.rate_source == "projected:2026-10-16"
    # promoted from a ~12-minute bench role to a stated depth-1 slot: more projected
    # minutes, so his scoring impact rises against the same pool
    assert now.categories["pts"] > was.categories["pts"]


def test_projected_basis_line_names_the_projection_date_and_the_caveat():
    """The projected basis is labeled wherever it renders: the projection date plus the
    2.11 unproven-edge caveat, and board_json carries both fields for the site."""
    board = build_board(_projected_store(), SEASON, pool_size=5,
                        availability=AvailabilityMode.PROJECTED, as_of="2026-10-16",
                        forward_season="2026-27", rate_basis="projected")
    assert "as of 2026-10-16" in board.basis
    assert "unproven" in board.basis
    assert "priced per-game outside the ranked season" in board.basis
    j = board_json(board)
    assert j["rate_basis"] == "projected"
    assert j["projected_as_of"] == "2026-10-16"


def test_unknown_rate_basis_is_rejected():
    with pytest.raises(ValueError, match="unknown rate basis"):
        build_board(_pool_store(), SEASON, pool_size=4, rate_basis="vibes")


def test_projected_rates_cannot_combine_with_realized_availability():
    with pytest.raises(ValueError, match="cannot combine"):
        build_board(_pool_store(), SEASON, pool_size=4,
                    availability=AvailabilityMode.REALIZED, rate_basis="projected")


def test_projected_rates_need_a_forward_season_and_an_as_of():
    with pytest.raises(ValueError, match="forward season"):
        build_board(_pool_store(), SEASON, pool_size=4,
                    availability=AvailabilityMode.NEUTRAL,
                    rate_basis="projected", as_of="2026-10-16")
    with pytest.raises(ValueError, match="as-of"):
        build_board(_projected_store(), SEASON, pool_size=5,
                    availability=AvailabilityMode.NEUTRAL,
                    forward_season="2026-27", rate_basis="projected")
