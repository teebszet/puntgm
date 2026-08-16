"""A14: percentage-rate shrinkage — the mechanism, and the measured decision to leave it off.

The shrinkage machinery works and is unit-tested here, but it is **disabled by default**
because turning it on measurably degraded the engine (fg_pct 77.4% -> 70.2%, ft_pct 52.3% ->
41.4% over 1,990 graded calls). These tests pin both facts: the mechanism behaves correctly
when enabled, and the shipped default is off.
"""

from __future__ import annotations

import fantasy_gm.config as cfg
import fantasy_gm.engine.projection as proj_mod
from fantasy_gm.config import Config
from fantasy_gm.data.store import Store
from fantasy_gm.engine.projection import Projector
from fantasy_gm.models import Game, PlayerGameLog
from fantasy_gm.valuation import clear_value_cache

SEASON = "2025-26"
DATES = [f"2025-11-{d:02d}" for d in range(1, 11)]
FUTURE = [f"2025-11-{d}" for d in (11, 12, 13, 14)]


def _store(shooter: dict) -> Store:
    """Pool shoots the league rate (80% FT on 10 attempts); ``shooter`` is the subject."""
    store = Store(":memory:")
    store.upsert_games(
        [Game(f"g{i}", SEASON, d, "LAL", "BOS", 1, 1) for i, d in enumerate(DATES)]
        + [Game(f"f{i}", SEASON, d, "LAL", "BOS", 1, 1) for i, d in enumerate(FUTURE)]
    )
    logs = []
    for i, d in enumerate(DATES):
        for p in range(20):
            logs.append(PlayerGameLog(f"g{i}", SEASON, d, f"pool{p}", f"Pool {p}", "LAL",
                                      {"ftm": 8.0, "fta": 10.0, "pts": 8.0}))
        logs.append(PlayerGameLog(f"g{i}", SEASON, d, "subject", "Subject", "LAL",
                                  dict(shooter, pts=shooter.get("ftm", 0.0))))
    store.upsert_player_logs(logs)
    clear_value_cache()
    return store


def _rate(store, shrink: bool) -> float:
    return Projector(Config(), shrink_percentages=shrink).team_projection(
        store, {}, ["subject"], DATES[-1], DATES[0], FUTURE[-1], ["ft_pct"], SEASON
    )["ft_pct"][0]


def _with_k(monkeypatch, k: float) -> None:
    monkeypatch.setattr(cfg, "PERCENTAGE_SHRINKAGE", {"ft_pct": k}, raising=False)
    monkeypatch.setattr(proj_mod, "PERCENTAGE_SHRINKAGE", {"ft_pct": k}, raising=False)


def test_shrinkage_is_disabled_by_default():
    """The measured decision: it was tried, it hurt, it is off. See config for the numbers."""
    assert cfg.PERCENTAGE_SHRINKAGE == {}
    store = _store({"ftm": 1.2, "fta": 1.3})
    assert _rate(store, True) == _rate(store, False)


def test_when_enabled_a_hot_low_volume_shooter_is_regressed(monkeypatch):
    """1.2-for-1.3 a night is a 92% trailing rate on 13 attempts — mostly luck."""
    _with_k(monkeypatch, 20.0)
    store = _store({"ftm": 1.2, "fta": 1.3})
    raw, shrunk = _rate(store, False), _rate(store, True)
    assert raw > 0.90
    assert shrunk < raw
    assert abs(shrunk - 0.802) < abs(raw - 0.802)


def test_when_enabled_volume_buys_credibility(monkeypatch):
    """k is denominated in attempts, so a high-volume shooter is shrunk proportionally less."""
    _with_k(monkeypatch, 20.0)
    low = _rate(_store({"ftm": 1.8, "fta": 2.0}), True)     # 90% on 20 attempts
    high = _rate(_store({"ftm": 9.0, "fta": 10.0}), True)   # 90% on 100 attempts
    assert high > low
    assert high > 0.85


def test_a_league_average_shooter_is_unchanged_by_shrinkage(monkeypatch):
    _with_k(monkeypatch, 20.0)
    store = _store({"ftm": 8.0, "fta": 10.0})
    assert abs(_rate(store, True) - _rate(store, False)) < 1e-6
