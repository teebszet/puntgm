"""Shared fixtures: an in-memory store seeded with a synthetic season + simulated league."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from fantasy_gm.data.simulate import simulate_league
from fantasy_gm.data.store import Store
from fantasy_gm.data.synthetic import seed_synthetic_season

SEASON = "2025-26"
MID_DATE = "2025-11-11"  # a date with prior games and an active matchup


@dataclass
class Fixture:
    store: Store
    league_id: str
    season: str
    as_of: str


def _build(season_seed: int = 7, league_seed: int = 1) -> Fixture:
    store = Store(":memory:")
    seed_synthetic_season(store, season=SEASON, seed=season_seed)
    league_id = simulate_league(store, season=SEASON, seed=league_seed, n_teams=8, roster_size=10)
    return Fixture(store, league_id, SEASON, MID_DATE)


@pytest.fixture
def fx() -> Fixture:
    return _build()


@pytest.fixture
def build():
    """Factory so a test can build multiple independent stores (e.g. reproducibility)."""
    return _build
