"""Forward-season player projections.

The draft engine consumes projections *only* through :class:`ProjectionSource`, so the
optimizer can be built and validated before any real projection model exists (design D11:
draft replay feeds the engine a completed season's realized production instead).

Implementations:
  * :class:`FixtureProjectionSource` — fixed values, for deterministic tests.
  * :class:`ActualsProjectionSource` — a completed season's realized production, for replay.
  * :class:`DerivedProjectionSource` — the minutes/role model that ships (design D8).
"""

from fantasy_gm.projections.actuals import ActualsProjectionSource, LookaheadError
from fantasy_gm.projections.derived import DerivedProjectionSource
from fantasy_gm.projections.fixture import FixtureProjectionSource
from fantasy_gm.projections.source import (
    CategoryEstimate,
    PlayerProjection,
    ProjectionBasis,
    ProjectionSource,
)

__all__ = [
    "ActualsProjectionSource",
    "CategoryEstimate",
    "DerivedProjectionSource",
    "FixtureProjectionSource",
    "LookaheadError",
    "PlayerProjection",
    "ProjectionBasis",
    "ProjectionSource",
]
