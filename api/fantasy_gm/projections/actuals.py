"""An oracle projection source: a completed season's *realized* production.

This is what makes design D11 work. Draft replay grades strategies against each other
over a season that already happened, so it needs no forecast — it can hand the engine
the truth. That decouples the optimizer (Track A) from the projection model (Track B),
which is the long pole.

**This source is a deliberate lookahead oracle and must never appear in a live path.**
It ignores ``as_of`` for the season it is projecting, by design. ``replay_only`` is set
so callers can assert against it; the constructor also refuses a season that has not
finished relative to ``as_of``, which catches the obvious misuse.
"""

from __future__ import annotations

from collections.abc import Sequence

from fantasy_gm.config import DEFAULT_CATEGORIES
from fantasy_gm.projections.source import (
    CategoryEstimate,
    PlayerProjection,
    ProjectionBasis,
    ProjectionSource,
    projected_stat_keys,
)


class LookaheadError(RuntimeError):
    """Raised when an oracle source is asked for a season that is not yet complete."""


class ActualsProjectionSource(ProjectionSource):
    """Per-game means/σ computed from every game a player actually played in ``season``.

    ``min_games`` drops players with too little signal to be worth drafting, matching the
    ``rosterable_pool`` floor in ``valuation``.
    """

    name = "actuals"
    replay_only = True

    def __init__(self, store, season: str, *, min_games: int = 1):
        self._store = store
        self._season = season
        self._min_games = min_games
        self._keys = projected_stat_keys(DEFAULT_CATEGORIES)
        self._cache: dict[str, PlayerProjection] | None = None

    def _season_end(self) -> str | None:
        row = self._store.conn.execute(
            "SELECT MAX(game_date) AS d FROM player_logs WHERE season = ?", (self._season,)
        ).fetchone()
        return row["d"] if row else None

    def _load(self) -> dict[str, PlayerProjection]:
        if self._cache is not None:
            return self._cache
        import json
        import statistics

        per_player: dict[str, list[dict]] = {}
        for r in self._store.conn.execute(
            "SELECT player_id, stats_json FROM player_logs WHERE season = ?", (self._season,)
        ):
            per_player.setdefault(r["player_id"], []).append(json.loads(r["stats_json"]))

        out: dict[str, PlayerProjection] = {}
        for pid, games in per_player.items():
            if len(games) < self._min_games:
                continue
            n = len(games)
            estimates = {}
            for key in self._keys:
                vals = [float(g.get(key, 0.0)) for g in games]
                mean = statistics.fmean(vals)
                std = statistics.pstdev(vals) if n > 1 else 0.0
                # Realized production is known exactly, so the mean carries no
                # estimation error — the one term an oracle legitimately zeroes out.
                estimates[key] = CategoryEstimate(key, mean, std, 0.0)
            out[pid] = PlayerProjection(
                player_id=pid,
                season=self._season,
                estimates=estimates,
                expected_games=float(n),
                expected_games_std=0.0,
                basis=ProjectionBasis.ACTUALS,
                source=self.name,
            )
        self._cache = out
        return out

    def _guard(self, season: str, as_of: str) -> None:
        if season != self._season:
            raise ValueError(f"{self.name} source is bound to {self._season}, asked for {season}")
        end = self._season_end()
        if end is not None and as_of < end:
            raise LookaheadError(
                f"{self.name} is a replay oracle: season {season} runs through {end}, "
                f"but as_of={as_of} is inside it. Use a forecasting source for live paths."
            )

    def project(
        self,
        season: str,
        as_of: str,
        player_ids: Sequence[str] | None = None,
    ) -> dict[str, PlayerProjection]:
        self._guard(season, as_of)
        loaded = self._load()
        if player_ids is None:
            return dict(loaded)
        return {p: loaded[p] for p in player_ids if p in loaded}

    def pool(self, season: str, as_of: str) -> list[str]:
        self._guard(season, as_of)
        return sorted(self._load())
