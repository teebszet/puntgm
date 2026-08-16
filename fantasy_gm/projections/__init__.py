"""Forward-season player projections.

The draft engine consumes projections *only* through :class:`ProjectionSource`, so the
optimizer can be built and validated before any real projection model exists (design D11:
draft replay feeds the engine a completed season's realized production instead).

Implementations:
  * :class:`FixtureProjectionSource` — fixed values, for deterministic tests.
  * ``ActualsProjectionSource``      — a completed season's realized production, for replay.
  * (Track B) the derived minutes/role model — the one that ships.
"""

from fantasy_gm.projections.source import (
    CategoryEstimate,
    PlayerProjection,
    ProjectionBasis,
    ProjectionSource,
)

__all__ = [
    "CategoryEstimate",
    "PlayerProjection",
    "ProjectionBasis",
    "ProjectionSource",
]
