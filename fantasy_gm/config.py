"""Project configuration: seasons, scoring categories, lineup cadence, paths, weights.

Kept deliberately declarative so both the CLI and tests share one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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
# --- Percentage-rate shrinkage (A14) — MEASURED AND REJECTED, kept empty -----
# Prior attempts to blend a projected shooting rate toward the league rate:
# rate = (makes + k·league_rate) / (attempts + k). The motivation was real — the engine's
# FT%-targeted adds had a trailing rate of 0.944 and a realized rate of 0.833 (league
# 0.802), so the projected edge was almost entirely sampling noise. Per-category k values
# minimising attempt-weighted forward-rate MAE were ft_pct 20, fg_pct 160.
#
# **It made the engine worse and was reverted.** On 1,990 graded calls, fg_pct fell 77.4% ->
# 70.2% and ft_pct 52.3% -> 41.4%. Shrinking every player toward the league rate compresses
# the *differences between candidates*, which is the only thing the ranking has to work with;
# it also pushes both teams' projected percentages together, so more categories read as
# contested and the engine targeted FT% *more* often (111 -> 119 slots) with less
# discrimination. Minimising rate-prediction error is not the same objective as ranking
# candidates, and optimising the former degraded the latter.
#
# Left as an empty dict (not deleted) so the mechanism stays available and the negative
# result stays documented. Populate to re-enable per category.
PERCENTAGE_SHRINKAGE: dict[str, float] = {}

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
    data_dir: Path = Path("data")
    seasons: list[str] = field(default_factory=lambda: list(ALL_SEASONS))
    primary_season: str = PRIMARY_SEASON
    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    default_cadence: str = DEFAULT_CADENCE
    recent_games_window: int = 10  # games used to estimate recent production
    # Games used to estimate the probability a player appears at all (A13). Deliberately
    # shorter than recent_games_window: measured on 2025-26, trailing-5 predicts forward
    # participation better than 10 or 20 (MAE 0.163 / 0.179 / 0.207) because availability
    # is a current-state fact (injury, rotation) while production is a stable skill.
    participation_window: int = 5
    # Categories the engine may not choose as a move's *target* (A15). A waiver add can
    # still help them incidentally — this only stops the engine spending its one move
    # chasing a category the wire cannot actually move.
    #
    # ft_pct is excluded on measurement, not taste. Across three engine versions it graded
    # 50.0% / 52.3% / 41.4% — at or below chance every time — while every other category sat
    # at 92-97%. The cause is not predictability (FT impact is *more* autocorrelated than
    # FG%, r=0.379 vs 0.321) but availability: the engine's FT%-targeted adds realized a
    # median impact of **+0.00** on 5 attempts, because a week's worth of free throws is
    # smaller than the swing of one made-vs-missed shot, and the wire's genuinely good FT
    # shooters are already rostered. Bucketing by attempt volume does not rescue it
    # (51.4% / 51.4% / 54.1% low/mid/high), and rate shrinkage made it worse (see
    # PERCENTAGE_SHRINKAGE).
    non_actionable_categories: frozenset[str] = frozenset({"ft_pct"})
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
