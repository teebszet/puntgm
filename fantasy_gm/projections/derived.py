"""The projection source that ships: minutes/role → rates → season value (design D8).

This is the own-built implementation behind :class:`ProjectionSource`. Licensing is why it
exists rather than a bought feed (FantasyPros is non-commercial-use-only, ESPN's fantasy
endpoints are unlicensed, DARKO's terms are unstated), and the pluggable interface is what
keeps a licensed source usable for private evaluation without the engine caring.

The pipeline, per player:

1. :mod:`minutes` projects per-game minutes from the player's own history and their stated
   forward depth-chart position, combined by inverse variance.
2. :mod:`rates` projects each category as a shrunk per-minute rate × those minutes, and
   propagates both uncertainties through the product.
3. :mod:`availability` projects expected games played as a separate output with its own band.
4. :mod:`rookies` handles players with no NBA history, on an explicitly labeled prior.

Everything reads through ``as_of``. That is structural, not a convention: the fits themselves
are built from ``store.player_game_stream_asof(as_of)``, so a source constructed for a draft
date physically cannot see a game played after it. The backtest requirement in
``player-projections`` depends on that being true by construction.

Percentage categories are never projected directly (A8): the source emits ``fgm``/``fga``/
``ftm``/``fta`` and the ratio is taken volume-weighted downstream.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fantasy_gm.config import DEFAULT_CATEGORIES
from fantasy_gm.projections.availability import GamesModel, fit_games
from fantasy_gm.projections.minutes import (
    MinutesModel,
    _player_windows,
    _rotation_ranks,
    fit_minutes,
)
from fantasy_gm.projections.rates import RatesModel, fit_rates, tier_of
from fantasy_gm.projections.rookies import fit_rookie_prior, project_rookie_minutes
from fantasy_gm.projections.source import (
    CategoryEstimate,
    PlayerProjection,
    ProjectionBasis,
    ProjectionSource,
    projected_stat_keys,
)

FULL_SEASON_GAMES = 82


@dataclass
class _Fit:
    """Everything fit for one (season, as_of) pair — built once, reused per player."""

    minutes: object
    rates: object
    games: object
    rookies: object
    history: dict[str, list[dict]]
    ranks: dict[str, int]
    team_games: dict[str, int]


class DerivedProjectionSource(ProjectionSource):
    """Forward-season projections derived from this project's own store.

    ``overrides`` supplies hand-set per-game values for specific players (``{player_id:
    {stat_key: per_game_mean, "minutes": …, "games": …}}``); anything overridden is stamped
    ``ProjectionBasis.OVERRIDE`` so a manual number is never reported as a modeled one.
    """

    name = "derived"

    def __init__(
        self,
        store,
        *,
        categories: Sequence[str] | None = None,
        window: int = 82,
        min_games: int = 10,
        season_games: int = FULL_SEASON_GAMES,
        overrides: Mapping[str, Mapping[str, float]] | None = None,
    ):
        self._store = store
        self._keys = projected_stat_keys(categories or DEFAULT_CATEGORIES)
        self._window = window
        self._min_games = min_games
        self._season_games = season_games
        self._overrides = {p: dict(v) for p, v in (overrides or {}).items()}
        self._fits: dict[tuple[str, str], _Fit] = {}

    # --- fitting ------------------------------------------------------------

    def fit(self, season: str, as_of: str) -> _Fit:
        """Fit every component from games known on or before ``as_of`` (memoized)."""
        key = (season, as_of)
        cached = self._fits.get(key)
        if cached is not None:
            return cached

        stream = self._store.player_game_stream_asof(as_of)
        history = _player_windows(stream, self._window)
        ranks = _rotation_ranks(history, self._min_games)
        minutes_fit = fit_minutes(self._store, as_of, window=self._window,
                                  min_games=self._min_games)
        rates_fit = fit_rates(self._store, as_of, self._keys, ranks,
                              window=self._window, min_games=self._min_games)
        games_fit = fit_games(self._store, as_of)
        rookie_prior = fit_rookie_prior(self._store, season, as_of, ranks)

        # Team games each player was available for, over their own observed window — the
        # denominator of the availability rate.
        team_games: dict[str, int] = {}
        for pid, games in history.items():
            team_games[pid] = self._store.games_in_window_for_team(
                games[-1]["team"], games[0]["game_date"], as_of
            )

        fit = _Fit(minutes_fit, rates_fit, games_fit, rookie_prior, history, ranks, team_games)
        self._fits[key] = fit
        return fit

    def minutes_projection(self, season: str, as_of: str, player_id: str):
        """The minutes projection behind a player's line, or None with no history.

        Minutes are not part of the :class:`ProjectionSource` contract — the engine consumes
        categories — but they are the model's dominant term, so the backtest scores them
        directly rather than inferring them from the categories they produced.
        """
        fit = self.fit(season, as_of)
        history = fit.history.get(player_id)
        if not history:
            return None
        fwd = self._store.forward_roster_asof(player_id, season, as_of)
        moved = bool(self._store.transactions_asof(season, as_of, player_id=player_id))
        if fwd is not None:
            moved = moved or fwd.team != history[-1]["team"]
        return MinutesModel(fit.minutes).project(
            player_id, history,
            stated_rank=fwd.depth_chart_pos if fwd else None,
            team_changed=moved,
            observed_rank=fit.ranks.get(player_id),
        )

    # --- ProjectionSource ---------------------------------------------------

    def pool(self, season: str, as_of: str) -> list[str]:
        """Players this source can project: enough history to fit, plus incoming players."""
        fit = self.fit(season, as_of)
        out = {p for p, games in fit.history.items() if len(games) >= self._min_games}
        out.update(p.player_id for p in self._store.incoming_players_asof(season, as_of))
        out.update(self._overrides)
        return sorted(out)

    def project(
        self,
        season: str,
        as_of: str,
        player_ids: Sequence[str] | None = None,
    ) -> dict[str, PlayerProjection]:
        fit = self.fit(season, as_of)
        wanted = list(player_ids) if player_ids is not None else self.pool(season, as_of)
        incoming = {p.player_id: p for p in self._store.incoming_players_asof(season, as_of)}

        out: dict[str, PlayerProjection] = {}
        for pid in wanted:
            if pid in self._overrides:
                out[pid] = self._override_projection(pid, season)
            elif fit.history.get(pid):
                out[pid] = self._modeled_projection(pid, season, as_of, fit)
            elif pid in incoming:
                out[pid] = self._rookie_projection(pid, season, as_of, fit, incoming[pid])
        return out

    # --- the three paths ----------------------------------------------------

    def _season_games_for(self, player_id: str, season: str, as_of: str, fallback: str = "") -> int:
        """Scheduled games for the player's forward team, if the store holds that season's
        schedule; otherwise a full season. A draft happens before any of it is played."""
        fwd = self._store.forward_roster_asof(player_id, season, as_of)
        team = fwd.team if fwd else fallback
        if not team:
            return self._season_games
        n = self._store.conn.execute(
            """SELECT COUNT(*) AS n FROM games
               WHERE season = ? AND (home_team = ? OR away_team = ?)""",
            (season, team, team),
        ).fetchone()["n"]
        return int(n) or self._season_games

    def _modeled_projection(self, pid: str, season: str, as_of: str, fit: _Fit
                            ) -> PlayerProjection:
        history = fit.history[pid]
        fwd = self._store.forward_roster_asof(pid, season, as_of)
        moved = bool(self._store.transactions_asof(season, as_of, player_id=pid))
        if fwd is not None and history:
            moved = moved or fwd.team != history[-1]["team"]

        mins = MinutesModel(fit.minutes).project(
            pid, history,
            stated_rank=fwd.depth_chart_pos if fwd else None,
            team_changed=moved,
            observed_rank=fit.ranks.get(pid),
        )
        tier = tier_of(fwd.depth_chart_pos if fwd else fit.ranks.get(pid))
        rates = RatesModel(fit.rates)
        estimates = {
            key: _estimate(rates.project(key, history, projected_minutes=mins.minutes,
                                         minutes_stderr=mins.mean_stderr, tier=tier))
            for key in self._keys
        }
        season_games = self._season_games_for(pid, season, as_of,
                                              fallback=history[-1]["team"])
        games = GamesModel(fit.games, season_games=self._season_games).project(
            pid, len(history), fit.team_games.get(pid, 0), season_games=season_games,
        )
        notes = {
            "minutes": f"{mins.minutes:.1f}",
            "minutes_stderr": f"{mins.mean_stderr:.2f}",
            "role_weight": f"{mins.role_weight:.2f}",
            "observed_games": str(mins.observed_games),
        }
        if fwd is not None:
            notes["stated_depth"] = str(fwd.depth_chart_pos)
        if moved:
            notes["team_changed"] = "yes"
        if len(history) < self._min_games:
            notes["thin_history"] = f"{len(history)} games"
        return PlayerProjection(
            player_id=pid, season=season, estimates=estimates,
            expected_games=round(games.expected_games, 2),
            expected_games_std=round(games.expected_games_std, 2),
            basis=ProjectionBasis.MODELED, source=self.name, notes=notes,
        )

    def _rookie_projection(self, pid: str, season: str, as_of: str, fit: _Fit, incoming
                           ) -> PlayerProjection:
        rk = project_rookie_minutes(fit.rookies, fit.minutes, incoming.draft_pick)
        tier = tier_of(rk.rank)
        rates = RatesModel(fit.rates)
        estimates = {
            key: _estimate(rates.project(key, [], projected_minutes=rk.minutes,
                                         minutes_stderr=rk.mean_stderr, tier=tier))
            for key in self._keys
        }
        season_games = self._season_games_for(pid, season, as_of, fallback=incoming.draft_team)
        games = GamesModel(fit.games, season_games=self._season_games).project(
            pid, 0, 0, season_games=season_games
        )
        return PlayerProjection(
            player_id=pid, season=season, estimates=estimates,
            expected_games=round(games.expected_games, 2),
            expected_games_std=round(games.expected_games_std, 2),
            basis=ProjectionBasis.PRIOR, source=self.name,
            notes={
                "prior": "draft-slot",
                "prior_basis": rk.basis,
                "draft_slot": str(incoming.draft_pick or "undrafted"),
                "slot_bucket": rk.bucket,
                "assumed_rank": str(rk.rank),
                "minutes": f"{rk.minutes:.1f}",
                "minutes_stderr": f"{rk.mean_stderr:.2f}",
            },
        )

    def _override_projection(self, pid: str, season: str) -> PlayerProjection:
        raw = self._overrides[pid]
        minutes = float(raw.get("minutes", 0.0))
        estimates = {
            key: CategoryEstimate(key, float(raw.get(key, 0.0)),
                                  float(raw.get(f"{key}_std", 0.0)),
                                  float(raw.get(f"{key}_stderr", 0.0)))
            for key in self._keys
        }
        return PlayerProjection(
            player_id=pid, season=season, estimates=estimates,
            expected_games=float(raw.get("games", self._season_games)),
            expected_games_std=float(raw.get("games_std", 0.0)),
            basis=ProjectionBasis.OVERRIDE, source=self.name,
            notes={"override": "manual", "minutes": f"{minutes:.1f}"},
        )


def _estimate(r) -> CategoryEstimate:
    return CategoryEstimate(r.key, round(r.per_game_mean, 4), round(r.per_game_std, 4),
                            round(r.mean_stderr, 4))
