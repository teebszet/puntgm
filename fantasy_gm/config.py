"""Project configuration: seasons, scoring categories, lineup cadence, paths, weights.

Kept deliberately declarative so both the CLI and tests share one source of truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- Store location ----------------------------------------------------------
# ``data/`` is resolved relative to the current working directory and is git-ignored,
# so a git worktree starts with an empty store and would need its own (slow) backfill.
# Setting FANTASY_GM_DATA_DIR lets parallel worktrees share the one backfilled store:
#
#     export FANTASY_GM_DATA_DIR=/Users/you/projects/fantasy-nba-gm/data
#
# SQLite tolerates concurrent readers fine; avoid running two backfills against a
# shared store at once.
DATA_DIR_ENV = "FANTASY_GM_DATA_DIR"


def default_data_dir() -> Path:
    """Store location: ``$FANTASY_GM_DATA_DIR`` if set, else ``./data``."""
    return Path(os.environ.get(DATA_DIR_ENV) or "data")

# --- Seasons -----------------------------------------------------------------
# Primary backfill target for this milestone; the validation set (the user's own
# leagues, D7) reaches back up to three seasons where retrievable.
PRIMARY_SEASON = "2025-26"
VALIDATION_SEASONS = ["2024-25", "2023-24"]
ALL_SEASONS = [PRIMARY_SEASON, *VALIDATION_SEASONS]

# --- Scoring categories (standard H2H 9-cat) ---------------------------------
# direction: +1 = higher is better, -1 = lower is better (turnovers).
CATEGORY_DIRECTION: dict[str, int] = {
    "pts": +1,
    "reb": +1,
    "ast": +1,
    "stl": +1,
    "blk": +1,
    "fg3m": +1,
    "fg_pct": +1,
    "ft_pct": +1,
    "tov": -1,
}
DEFAULT_CATEGORIES = list(CATEGORY_DIRECTION.keys())

# --- Category variance ------------------------------------------------------
# There is deliberately NO hard-coded per-category variance grouping. Real 2025-26 data
# (see assumptions ledger A1/A2/A4) showed the projector's measured per-player per-game σ
# already captures category volatility, and game-to-game production is ~independent, so a
# category multiplier would double-count. Relative variance is *measured* for reporting via
# fantasy_gm.validation.measure_category_cv, not asserted here.

# Percentage categories are volume-weighted (A8): the value is Σmakes / Σattempts, never
# a sum of per-game percentages. Maps the category to its (makes, attempts) component keys.
PERCENTAGE_CATEGORIES: dict[str, tuple[str, str]] = {
    "fg_pct": ("fgm", "fga"),
    "ft_pct": ("ftm", "fta"),
}

# --- Projection / signal thresholds ------------------------------------------
SAFE_PROB = 0.80   # win prob >= -> "safe"
GONE_PROB = 0.20   # win prob <= -> "gone"; between -> "contested"
STRONG_STRENGTH = 0.45  # signal strength >= (plus a sustained, causal trend) -> "strong"

# --- Season stage (relevance weighting, D6) ----------------------------------
# Fractions of the season elapsed. Early boosts usage-breakout signals; late boosts
# pure schedule/matchup-securing signals.
EARLY_STAGE_MAX = 0.30
LATE_STAGE_MIN = 0.70

# --- Lineup cadence ----------------------------------------------------------
# The user's own league moved weekly-lock -> daily-change ~2 seasons ago (D9), so
# cadence is a per-league setting and the harness must segment seasons by it.
CADENCE_WEEKLY = "weekly-lock"
CADENCE_DAILY = "daily-change"
VALID_CADENCES = (CADENCE_WEEKLY, CADENCE_DAILY)
DEFAULT_CADENCE = CADENCE_WEEKLY


@dataclass(frozen=True)
class ScoringWeights:
    """Deterministic skeleton-engine weights. The matchup/category tilt defaults low so
    the engine stays a pure baseline (D5) — the plumbing exists for the next engine."""

    games_in_window: float = 4.0
    recent_production: float = 1.0
    out_penalty: float = 1000.0  # effectively removes OUT players
    questionable_penalty: float = 8.0
    winnable_category_tilt: float = 0.5  # small nudge toward winnable-cat production


@dataclass(frozen=True)
class Config:
    data_dir: Path = field(default_factory=default_data_dir)
    seasons: list[str] = field(default_factory=lambda: list(ALL_SEASONS))
    primary_season: str = PRIMARY_SEASON
    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    default_cadence: str = DEFAULT_CADENCE
    recent_games_window: int = 10  # games used to estimate recent production
    weights: ScoringWeights = field(default_factory=ScoringWeights)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "fantasy_gm.sqlite"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "raw_cache"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
