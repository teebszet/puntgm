"""Expected games played, as a first-class projection output (design D7).

A per-game rate is worth nothing in a category league if the player is not on the floor.
Season replay found that **31% of waiver adds never played a game** in the target period,
and the draft has exactly the same exposure: two players with identical per-game lines are
not worth the same pick if one plays 78 games and the other plays 45.

The model is a beta-binomial fit over the pool. A player's availability rate is
games-played / games-their-team-played, shrunk toward the pool's rate by a Beta prior whose
parameters are fit by moments from the pool itself — so a player with 12 games of history
is pulled toward the league rate and a player with 300 is not. Expected games played is that
posterior rate times the target season's scheduled games, and it carries a variance with
both terms: uncertainty in the *rate* and the binomial spread around it.

``measure_games_production_correlation`` is the ledger check for A-DRAFT-7 — the claim that
value factorizes as ``E[games] × E[per-game]``. It measures rather than assumes.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

MIN_PLAYERS_FOR_FIT = 20
FULL_SEASON_GAMES = 82

# Used only when the pool cannot identify the prior; recorded as a fallback basis.
FALLBACK_PRIOR_STRENGTH = 20.0


@dataclass(frozen=True)
class GamesFit:
    """Measured beta-binomial prior on a player's availability rate."""

    as_of: str
    alpha: float
    beta: float
    pool_rate: float
    n_players: int
    basis: str = "measured"      # "measured" | "fallback"

    @property
    def prior_games(self) -> float:
        """Prior weight in games — how much history it takes to outvote the pool rate."""
        return self.alpha + self.beta


@dataclass(frozen=True)
class GamesProjection:
    player_id: str
    expected_games: float
    expected_games_std: float
    availability_rate: float
    observed_games: int
    team_games: int


def _team_games(store, team: str, start: str, as_of: str) -> int:
    return store.games_in_window_for_team(team, start, as_of) if team else 0


def fit_games(store, as_of: str, *, since: str | None = None, min_games: int = 5) -> GamesFit:
    """Fit the availability prior from every game known on or before ``as_of``.

    ``since`` bounds the observation window; without it, availability is measured over the
    whole history the store holds, which under-rates players who only recently entered it.
    """
    rows = store.player_game_stream_asof(as_of, since=since)
    per_player: dict[str, list[dict]] = {}
    for r in rows:
        per_player.setdefault(r["player_id"], []).append(r)

    rates: list[float] = []
    for games in per_player.values():
        if len(games) < min_games:
            continue
        team = games[-1]["team"]
        start = since or games[0]["game_date"]
        available = _team_games(store, team, start, as_of)
        if available <= 0:
            continue
        rates.append(min(len(games) / available, 1.0))

    if len(rates) < MIN_PLAYERS_FOR_FIT:
        pool = statistics.fmean(rates) if rates else 0.8
        return GamesFit(as_of, pool * FALLBACK_PRIOR_STRENGTH,
                        (1 - pool) * FALLBACK_PRIOR_STRENGTH, pool, len(rates), "fallback")

    mean = statistics.fmean(rates)
    var = statistics.pvariance(rates)
    # Method of moments for a Beta: strength = mean(1-mean)/var - 1. When the observed spread
    # exceeds what a Beta can carry the estimate goes non-positive; fall back rather than
    # emit a negative prior.
    if var <= 0 or mean <= 0 or mean >= 1:
        return GamesFit(as_of, mean * FALLBACK_PRIOR_STRENGTH,
                        (1 - mean) * FALLBACK_PRIOR_STRENGTH, mean, len(rates), "fallback")
    strength = mean * (1 - mean) / var - 1.0
    if strength <= 0:
        return GamesFit(as_of, mean * FALLBACK_PRIOR_STRENGTH,
                        (1 - mean) * FALLBACK_PRIOR_STRENGTH, mean, len(rates), "fallback")
    return GamesFit(as_of, mean * strength, (1 - mean) * strength, mean, len(rates))


class GamesModel:
    """Projects expected games played for the upcoming season from a :class:`GamesFit`."""

    def __init__(self, fit: GamesFit, *, season_games: int = FULL_SEASON_GAMES):
        self.fit = fit
        self.season_games = season_games

    def project(
        self, player_id: str, observed_games: int, team_games: int,
        *, season_games: int | None = None,
    ) -> GamesProjection:
        f = self.fit
        n = max(team_games, observed_games)
        rate = (observed_games + f.alpha) / (n + f.alpha + f.beta) if (n + f.prior_games) > 0 \
            else f.pool_rate
        total = f.alpha + f.beta + n
        rate_var = rate * (1 - rate) / (total + 1) if total > 0 else 0.0
        target = float(season_games if season_games is not None else self.season_games)
        expected = rate * target
        # Two sources of spread: how well the rate is known, and the binomial scatter of
        # games around it even if the rate were exact.
        var = target ** 2 * rate_var + target * rate * (1 - rate)
        return GamesProjection(
            player_id=player_id,
            expected_games=expected,
            expected_games_std=var ** 0.5,
            availability_rate=rate,
            observed_games=observed_games,
            team_games=team_games,
        )


# --- ledger check: A-DRAFT-7 (is E[games] separable from E[per-game]?) --------


def measure_games_production_correlation(
    store, season: str, *, min_games: int = 10
) -> dict[str, float]:
    """Measure whether availability and production are actually separable (A-DRAFT-7).

    Three statistics, none of them assumed:

    * ``corr_games_minutes`` — across players, do the players who play more games also play
      more minutes per game? A strong positive says the factorization double-counts role.
    * ``corr_games_scoring`` — the same for per-game points, as a production proxy.
    * ``post_absence_minutes_ratio`` — within player, minutes in the first games back after a
      missed stretch relative to that player's own average. Below 1.0 is the load-management
      / injury-return effect the factorization ignores.
    """
    rows = store.player_game_stream_asof("9999-12-31", season=season)
    per_player: dict[str, list[dict]] = {}
    for r in rows:
        per_player.setdefault(r["player_id"], []).append(r)

    played: list[float] = []
    minutes: list[float] = []
    scoring: list[float] = []
    ratios: list[float] = []
    for games in per_player.values():
        if len(games) < min_games:
            continue
        mins = [g["minutes"] for g in games if g["minutes"] is not None]
        if not mins:
            continue
        played.append(float(len(games)))
        minutes.append(statistics.fmean(mins))
        scoring.append(statistics.fmean([g["stats"].get("pts", 0.0) for g in games]))
        ratios.extend(_post_absence_ratios(games, statistics.fmean(mins)))

    out = {
        "n_players": float(len(played)),
        "n_returns": float(len(ratios)),
        "corr_games_minutes": _corr(played, minutes),
        "corr_games_scoring": _corr(played, scoring),
    }
    if ratios:
        out["post_absence_minutes_ratio"] = statistics.fmean(ratios)
    return out


def _post_absence_ratios(
    games: list[dict], player_mean: float, gap_days: int = 8, n_back: int = 3
) -> list[float]:
    """Minutes in the first ``n_back`` games after a gap of ``gap_days``, over the player's own
    average. A gap in *game dates* is the only absence signal box scores carry."""
    from datetime import date

    if player_mean <= 0:
        return []
    out: list[float] = []
    for i in range(1, len(games)):
        prev = date.fromisoformat(games[i - 1]["game_date"])
        cur = date.fromisoformat(games[i]["game_date"])
        if (cur - prev).days < gap_days:
            continue
        after = [g["minutes"] for g in games[i:i + n_back] if g["minutes"] is not None]
        if after:
            out.append(statistics.fmean(after) / player_mean)
    return out


def _corr(a: list[float], b: list[float]) -> float:
    if len(a) < 2:
        return 0.0
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    if sa == 0 or sb == 0:
        return 0.0
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / len(a)
    return round(cov / (sa * sb), 4)
