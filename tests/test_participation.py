"""Games-played model (A13): scheduled team games are not played player games."""

from __future__ import annotations

import math

from fantasy_gm.config import Config
from fantasy_gm.data.store import Store
from fantasy_gm.engine.projection import Projector
from fantasy_gm.models import Game, PlayerGameLog

SEASON = "2025-26"
DATES = [f"2025-11-{d:02d}" for d in range(1, 11)]   # 10 scheduled LAL games


def _store(played: list[str], pts: float = 10.0, sd_dates: dict | None = None) -> Store:
    """A store where LAL plays on every date in DATES and the player appears on ``played``."""
    store = Store(":memory:")
    store.upsert_games([
        Game(f"g{i}", SEASON, d, "LAL", "BOS", 100, 99) for i, d in enumerate(DATES)
    ])
    store.upsert_player_logs([
        PlayerGameLog(f"g{DATES.index(d)}", SEASON, d, "p1", "Player One", "LAL",
                      {"pts": (sd_dates or {}).get(d, pts)})
        for d in played
    ])
    return store


def test_participation_rate_is_appearances_over_scheduled_games():
    store = _store(DATES[:3])          # played 3 of the last 5 scheduled (dates 1-5)
    assert store.participation_rate("p1", DATES[4], window=5) == 0.6


def test_full_and_zero_participation():
    assert _store(DATES).participation_rate("p1", DATES[9], window=5) == 1.0
    # appeared early, then stopped: the last 5 scheduled games have no appearances
    store = _store(DATES[:2])
    assert store.participation_rate("p1", DATES[9], window=5) == 0.0


def test_unknown_player_has_no_rate():
    store = _store(DATES)
    assert store.participation_rate("nobody", DATES[9], window=5) is None


def test_window_is_scheduled_games_not_calendar_days():
    """Only the team's last N *scheduled* games count, regardless of the gaps between."""
    store = _store([DATES[5], DATES[7], DATES[9]])
    # last 5 scheduled = dates 6..10, of which the player appeared in 3
    assert store.participation_rate("p1", DATES[9], window=5) == 0.6


def test_projection_scales_with_participation():
    """A player who appears half the time projects half the production."""
    cfg = Config()
    full = _store(DATES)
    half = _store(DATES[::2])          # every other game -> q = 0.4 over the last 5

    as_of, end = DATES[9], "2025-11-20"
    for s in (full, half):
        s.upsert_games([Game(f"f{i}", SEASON, f"2025-11-{d}", "LAL", "BOS", 1, 1)
                        for i, d in enumerate((11, 12, 13, 14))])

    def total(store):
        return store and Projector(cfg).team_projection(
            store, {}, ["p1"], as_of, DATES[0], end, ["pts"])["pts"][0]

    q_full = full.participation_rate("p1", as_of, window=5)
    q_half = half.participation_rate("p1", as_of, window=5)
    assert q_full == 1.0 and q_half == 0.4
    assert total(half) == total(full) * q_half


def test_participation_can_be_disabled_for_ab_testing():
    store = _store(DATES[::2])
    store.upsert_games([Game(f"f{i}", SEASON, f"2025-11-{d}", "LAL", "BOS", 1, 1)
                        for i, d in enumerate((11, 12, 13, 14))])
    cfg, as_of, end = Config(), DATES[9], "2025-11-20"
    on = Projector(cfg, participation=True).team_projection(
        store, {}, ["p1"], as_of, DATES[0], end, ["pts"])["pts"][0]
    off = Projector(cfg, participation=False).team_projection(
        store, {}, ["p1"], as_of, DATES[0], end, ["pts"])["pts"][0]
    assert on < off
    assert math.isclose(on, off * 0.4)


def test_dnp_risk_adds_variance_beyond_game_to_game_spread():
    """A perfectly consistent scorer who only plays half the time is NOT a certainty.

    With σ=0 the old model gave zero variance; the mixture term q(1−q)μ² is what captures
    the risk that he simply doesn't appear.
    """
    store = _store(DATES[::2], pts=20.0)   # identical 20 pts every game he plays -> σ = 0
    store.upsert_games([Game(f"f{i}", SEASON, f"2025-11-{d}", "LAL", "BOS", 1, 1)
                        for i, d in enumerate((11, 12, 13, 14))])
    cfg, as_of, end = Config(), DATES[9], "2025-11-20"

    _mean_on, sd_on = Projector(cfg, participation=True).team_projection(
        store, {}, ["p1"], as_of, DATES[0], end, ["pts"])["pts"]
    _mean_off, sd_off = Projector(cfg, participation=False).team_projection(
        store, {}, ["p1"], as_of, DATES[0], end, ["pts"])["pts"]

    assert sd_off == 0.0     # old model: consistent scorer = no uncertainty at all
    assert sd_on > 0.0       # new model: he might not play
