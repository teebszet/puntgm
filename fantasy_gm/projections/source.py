"""The projection contract between the draft engine and whatever produces projections.

This module is deliberately model-free: it fixes the *shape* of a projection so the
engine (Track A) and the derived projection model (Track B) can be built in parallel
without guessing at each other's interface.

Three things the engine needs that a plain per-game average does not carry:

1. **Production variance** (``per_game_std``) — the period-to-period spread that makes
   weekly H2H a different problem from season-long totals. This is the term z-score
   omits and the variance-aware basis restores.
2. **Uncertainty in the estimate of the mean** (``mean_stderr``) — distinct from (1).
   A player with 8 career games and one with 400 can share a mean and a game-to-game
   σ while differing enormously in how well that mean is *known*. The projector made
   exactly this correction for matchup projection; drafting needs it more, because a
   draft commits for a season.
3. **Expected games played** (``expected_games``) — a per-game rate is worth nothing in
   a category league if the player is not on the floor. Season replay found 31% of
   waiver adds never played a game in the target period; the draft has the same exposure.

Percentage categories follow the store's volume-weighted convention (config.A8): a
projection carries the *components* (``fgm``/``fga``/``ftm``/``fta``) rather than a bare
percentage, because Σmakes/Σattempts is not the mean of per-game percentages. Use
:func:`percentage_components` to find which component keys a source must supply.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from fantasy_gm.config import DEFAULT_CATEGORIES, PERCENTAGE_CATEGORIES


class ProjectionBasis(StrEnum):
    """How a projection was arrived at — carried so nothing asserted can masquerade
    as something measured (project standing rule; see the assumptions ledger)."""

    MODELED = "modeled"      # fit from this player's own history
    PRIOR = "prior"          # no usable history (e.g. a rookie) — a labeled prior
    OVERRIDE = "override"    # supplied by hand
    ACTUALS = "actuals"      # a completed season's realized production (replay only)
    FIXTURE = "fixture"      # fixed test values


def percentage_components() -> list[str]:
    """Component stat keys percentage categories are computed from (e.g. fgm, fga)."""
    out: list[str] = []
    for makes, attempts in PERCENTAGE_CATEGORIES.values():
        out.extend((makes, attempts))
    return out


def projected_stat_keys(categories: Sequence[str] | None = None) -> list[str]:
    """Every key a source must estimate: counting categories plus percentage components.

    Percentage categories themselves are *derived*, never projected directly.
    """
    cats = list(categories or DEFAULT_CATEGORIES)
    keys = [c for c in cats if c not in PERCENTAGE_CATEGORIES]
    keys.extend(k for k in percentage_components() if k not in keys)
    return keys


@dataclass(frozen=True)
class CategoryEstimate:
    """A projected per-game rate for one stat key, with both kinds of uncertainty."""

    category: str
    per_game_mean: float
    per_game_std: float = 0.0   # game-to-game production spread
    mean_stderr: float = 0.0    # uncertainty in the estimate of per_game_mean

    def season_total(self, games: float) -> float:
        return self.per_game_mean * games


@dataclass(frozen=True)
class PlayerProjection:
    """One player's forward-season projection.

    ``estimates`` is keyed by :func:`projected_stat_keys` — counting categories plus
    percentage components, never percentage categories themselves.
    """

    player_id: str
    season: str
    estimates: dict[str, CategoryEstimate]
    expected_games: float
    expected_games_std: float = 0.0
    basis: ProjectionBasis = ProjectionBasis.MODELED
    source: str = ""
    notes: dict[str, str] = field(default_factory=dict)

    def estimate(self, key: str) -> CategoryEstimate:
        """Estimate for ``key``, or an explicit zero rather than a KeyError — a source
        legitimately may not project every key for every player."""
        return self.estimates.get(key) or CategoryEstimate(key, 0.0)

    def season_total(self, key: str) -> float:
        return self.estimate(key).season_total(self.expected_games)

    def percentage(self, category: str) -> float | None:
        """Volume-weighted percentage for e.g. ``fg_pct``, or None if attempts are zero."""
        pair = PERCENTAGE_CATEGORIES.get(category)
        if pair is None:
            return None
        makes, attempts = (self.estimate(k).per_game_mean for k in pair)
        return makes / attempts if attempts else None

    @property
    def is_provisional(self) -> bool:
        """True when the projection is not fit from the player's own history."""
        return self.basis in (ProjectionBasis.PRIOR, ProjectionBasis.OVERRIDE)


class ProjectionSource(ABC):
    """Where forward-season projections come from.

    ``as_of`` is part of the contract, not a convenience: the backtest requirement is
    that projecting a season uses no information from inside it, and an implementation
    that ignores ``as_of`` cannot honour that. Implementations MUST NOT read facts
    recorded after ``as_of``.
    """

    #: Short identifier recorded on every projection and in the recommendation log.
    name: str = "unnamed"

    @abstractmethod
    def project(
        self,
        season: str,
        as_of: str,
        player_ids: Sequence[str] | None = None,
    ) -> dict[str, PlayerProjection]:
        """Project ``season`` as known on ``as_of``, keyed by player id.

        ``player_ids`` restricts the result; None means the source's full pool.
        """

    @abstractmethod
    def pool(self, season: str, as_of: str) -> list[str]:
        """Player ids this source can project for ``season`` as of ``as_of``."""

    def project_one(
        self, player_id: str, season: str, as_of: str
    ) -> PlayerProjection | None:
        return self.project(season, as_of, [player_id]).get(player_id)
