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
    all_builds,
    biggest_movers,
    board_json,
    build_board,
    export,
    project_availability,
    render_markdown,
)
from fantasy_gm.draft.xscore import PeriodStats
from fantasy_gm.models import Game, PlayerGameLog, UsageRole

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


def test_availability_scaling_is_binomial_over_games_not_bernoulli_over_weeks():
    """The ``/n`` in ``τ'² = r·τ² + r(1−r)·μ²/n`` is load-bearing.

    Without it the availability penalty is inflated by games-per-week (~3.5×), and because the
    term scales with μ² it lands almost entirely on high-production players — it ranked durable
    role players above every star. Pin the exact identity rather than the symptom.
    """
    from fantasy_gm.draft.board import _scale_for_availability

    stats = {"p": {"pts": PeriodStats(mean=100.0, std=20.0, periods=10)}}
    r, n = 0.8, 4.0
    scaled = _scale_for_availability(stats, {"p": r}, {"p": n})["p"]["pts"]

    assert scaled.mean == pytest.approx(r * 100.0)
    assert scaled.std == pytest.approx((r * 400.0 + r * (1 - r) * 10000.0 / n) ** 0.5)
    # And the Bernoulli-over-weeks form (no /n) would be strictly larger.
    bernoulli = (r * 400.0 + r * (1 - r) * 10000.0) ** 0.5
    assert scaled.std < bernoulli


def test_full_availability_leaves_stats_untouched():
    from fantasy_gm.draft.board import _scale_for_availability

    stats = {"p": {"pts": PeriodStats(mean=100.0, std=20.0, periods=10)}}
    scaled = _scale_for_availability(stats, {"p": 1.0}, {"p": 3.5})["p"]["pts"]
    assert scaled.mean == pytest.approx(100.0)
    assert scaled.std == pytest.approx(20.0)


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
