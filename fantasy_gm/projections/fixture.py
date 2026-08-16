"""A fixed-value projection source, so engine tests are deterministic and offline.

The draft engine's behaviour (roster-conditional valuation, emergent category
concentration, positional pricing) has to be testable without a projection model and
without a backfilled store. This supplies exactly that: hand-specified per-game rates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fantasy_gm.projections.source import (
    CategoryEstimate,
    PlayerProjection,
    ProjectionBasis,
    ProjectionSource,
    projected_stat_keys,
)


class FixtureProjectionSource(ProjectionSource):
    """Projections from a literal mapping of ``player_id -> {stat_key: per_game_mean}``.

    ``stds`` and ``mean_stderrs`` optionally override the uncertainty terms per player
    and key; anything unspecified defaults to ``default_std_fraction`` of the mean (a
    crude but non-zero spread, so variance-aware code paths are actually exercised) and
    zero mean-uncertainty.
    """

    name = "fixture"

    def __init__(
        self,
        rates: Mapping[str, Mapping[str, float]],
        *,
        games: Mapping[str, float] | float = 70.0,
        stds: Mapping[str, Mapping[str, float]] | None = None,
        mean_stderrs: Mapping[str, Mapping[str, float]] | None = None,
        default_std_fraction: float = 0.5,
        basis: ProjectionBasis = ProjectionBasis.FIXTURE,
    ):
        self._rates = {p: dict(r) for p, r in rates.items()}
        self._games = games
        self._stds = {p: dict(s) for p, s in (stds or {}).items()}
        self._stderrs = {p: dict(s) for p, s in (mean_stderrs or {}).items()}
        self._default_std_fraction = default_std_fraction
        self._basis = basis

    def _games_for(self, player_id: str) -> float:
        if isinstance(self._games, Mapping):
            return float(self._games.get(player_id, 0.0))
        return float(self._games)

    def project(
        self,
        season: str,
        as_of: str,
        player_ids: Sequence[str] | None = None,
    ) -> dict[str, PlayerProjection]:
        wanted = list(player_ids) if player_ids is not None else list(self._rates)
        out: dict[str, PlayerProjection] = {}
        for pid in wanted:
            rates = self._rates.get(pid)
            if rates is None:
                continue
            estimates = {}
            for key in projected_stat_keys():
                mean = float(rates.get(key, 0.0))
                std = self._stds.get(pid, {}).get(key, abs(mean) * self._default_std_fraction)
                stderr = self._stderrs.get(pid, {}).get(key, 0.0)
                estimates[key] = CategoryEstimate(key, mean, std, stderr)
            out[pid] = PlayerProjection(
                player_id=pid,
                season=season,
                estimates=estimates,
                expected_games=self._games_for(pid),
                basis=self._basis,
                source=self.name,
            )
        return out

    def pool(self, season: str, as_of: str) -> list[str]:
        return sorted(self._rates)
