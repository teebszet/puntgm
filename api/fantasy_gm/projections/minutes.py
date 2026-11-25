"""Projected minutes — the dominant term in fantasy value (design D8).

Per-game *rates* are the easy part. What actually moves a season projection is how many
minutes a player will be on the floor, and that is a function of role: where they sit in
their team's rotation going into the season, and whether an offseason move changed it.

The model combines two independent estimates of next season's minutes and weights them by
how much each is worth trusting:

1. **The player's own history** — a shrunk mean of their observed per-game minutes. Its
   error is the sampling error of that mean *plus* how much a player's true minutes drift
   between periods (a stable 30-minute starter is still not a 30-minute starter forever).
2. **Their stated role** — the depth-chart position recorded in ``forward_roster`` for the
   upcoming season, mapped through a measured curve of minutes-by-rotation-rank. Its error
   is the measured spread of minutes among players at that rank.

Both are combined by inverse variance, so nothing is asserted about which to believe: a
rookie-contract player with 12 games of history and a stated starting job leans on the role
curve, a 400-game veteran with an unchanged role leans on their own history, and the
combination is tighter than either. A player who changed teams gets a measured inflation of
the drift term — wider band, unshifted mean, because a trade tells us the situation moved
without telling us which way.

**Every parameter here is fit from the store** (project standing rule: nothing asserted stays
a constant once real data exists). Where the data cannot identify a parameter, the fallback
is labeled in ``MinutesFit.basis`` rather than quietly baked in.

Note on ``depth_chart_pos``: the store holds no player positions, so a "depth chart position"
is read as **rotation rank within the team** (1 = the team's biggest-minutes player). That is
the only reading the data supports, and it is what the curve below is fit against.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

# Rotation ranks past this are pooled into a single tail bucket — beyond the ~10-man
# rotation, minutes are near-zero and rank stops carrying information.
MAX_TRACKED_RANK = 12

# Minimum sample sizes below which a fit is not identifiable and a labeled fallback is used.
MIN_PLAYERS_FOR_FIT = 20
MIN_MOVERS_FOR_DRIFT = 20

# Candidate half-lives (in games) for the recency weighting of a player's own minutes. None
# means "weight every game equally". Which one wins is *fit* by held-out error inside the
# training window, never chosen by hand.
HALF_LIFE_GRID: tuple[float | None, ...] = (None, 60.0, 40.0, 25.0, 15.0, 10.0, 6.0)

# Fallbacks, used only when the store cannot identify the parameter. Each is recorded in
# MinutesFit.basis as "fallback" so a projection built on one is never mistaken for measured.
FALLBACK_DRIFT_VAR = 16.0            # (4 minutes)^2 of period-to-period role drift
FALLBACK_TEAM_CHANGE_MULT = 2.0      # a team change doubles drift variance


@dataclass(frozen=True)
class MinutesFit:
    """Measured parameters of the minutes model, as of a date."""

    as_of: str
    role_curve: dict[int, float]           # rotation rank -> median per-game minutes
    role_residual_var: dict[int, float]    # rotation rank -> variance of minutes at that rank
    tail_minutes: float                    # rank > MAX_TRACKED_RANK
    tail_residual_var: float
    pool_mean: float                       # shrinkage target for a thin history
    within_var: float                      # mean per-player game-to-game variance of minutes
    between_var: float                     # player-to-player variance of true mean minutes
    drift_var: float                       # period-to-period drift in a player's true minutes
    team_change_drift_mult: float          # drift inflation for a player who changed teams
    half_life: float | None                # recency half-life in games (None = flat weighting)
    n_players: int
    n_movers: int
    basis: dict[str, str] = field(default_factory=dict)  # parameter -> "measured" | "fallback"

    @property
    def shrinkage_games(self) -> float:
        """Prior weight in games: how many observed games it takes to half-trust the mean."""
        return self.within_var / self.between_var if self.between_var > 0 else 0.0

    def role(self, rank: int | None) -> tuple[float, float] | None:
        """(expected minutes, variance) for a rotation rank, or None if rank is unknown."""
        if rank is None:
            return None
        if rank > MAX_TRACKED_RANK:
            return self.tail_minutes, self.tail_residual_var
        mu = self.role_curve.get(rank)
        if mu is None:
            return self.tail_minutes, self.tail_residual_var
        return mu, self.role_residual_var.get(rank, self.tail_residual_var)


@dataclass(frozen=True)
class MinutesProjection:
    """One player's projected per-game minutes, with both kinds of uncertainty."""

    player_id: str
    minutes: float
    per_game_std: float      # game-to-game spread of minutes played
    mean_stderr: float       # uncertainty in the estimate of ``minutes`` itself
    observed_games: int
    observed_minutes: float  # the shrunk history-only estimate, before any role blend
    rotation_rank: int | None = None
    stated_rank: int | None = None
    team_changed: bool = False
    role_weight: float = 0.0  # share of the projection coming from the stated role


# --- fitting -----------------------------------------------------------------


def _player_windows(rows: list[dict], window: int) -> dict[str, list[dict]]:
    """Last ``window`` games per player from a player-game stream (already date-ordered)."""
    per: dict[str, list[dict]] = {}
    for r in rows:
        per.setdefault(r["player_id"], []).append(r)
    return {p: g[-window:] for p, g in per.items()}


def _minutes_of(games: list[dict]) -> list[float]:
    return [g["minutes"] for g in games if g["minutes"] is not None]


def ew_mean(values: list[float], half_life: float | None) -> tuple[float, float]:
    """Recency-weighted mean of ``values`` (oldest first) and its effective sample size.

    Minutes are not stationary: a player who moved into the starting five in January is
    better described by January than by the season. The effective sample size
    ``(Σw)²/Σw²`` is what the shrinkage step consumes, so leaning on recent games correctly
    costs confidence rather than being free.
    """
    if not values:
        return 0.0, 0.0
    if half_life is None:
        return statistics.fmean(values), float(len(values))
    n = len(values)
    weights = [0.5 ** ((n - 1 - i) / half_life) for i in range(n)]
    total = sum(weights)
    mean = sum(w * v for w, v in zip(weights, values, strict=True)) / total
    n_eff = total ** 2 / sum(w * w for w in weights)
    return mean, n_eff


def _fit_half_life(per_player: dict[str, list[dict]], min_games: int
                   ) -> tuple[float | None, str]:
    """Choose the recency half-life by held-out error *inside* the training window.

    Each player's window is split in half; a candidate half-life is scored by how well the
    recency-weighted mean of the first half predicts the plain mean of the second. The winner
    is the one that actually forecasts, which is the only question that matters. Nothing here
    touches the evaluation period — the split is internal to games already known.
    """
    samples: list[tuple[list[float], float]] = []
    for games in per_player.values():
        half = len(games) // 2
        a, b = _minutes_of(games[:half]), _minutes_of(games[half:])
        if len(a) >= min_games and len(b) >= min_games:
            samples.append((a, statistics.fmean(b)))
    if len(samples) < MIN_PLAYERS_FOR_FIT:
        return None, "fallback"
    best, best_err = None, float("inf")
    for hl in HALF_LIFE_GRID:
        err = statistics.fmean([abs(ew_mean(a, hl)[0] - target) for a, target in samples])
        if err < best_err:
            best, best_err = hl, err
    return best, "measured"


def _rotation_ranks(per_player: dict[str, list[dict]], min_games: int) -> dict[str, int]:
    """Rotation rank within the player's most recent team: 1 = biggest minutes.

    Ranking is by mean minutes over games played *for that team*, so a mid-season trade
    ranks the player where they ended up rather than blending two rotations.
    """
    by_team: dict[str, list[tuple[str, float]]] = {}
    for pid, games in per_player.items():
        team = games[-1]["team"]
        mins = [g["minutes"] for g in games if g["team"] == team and g["minutes"] is not None]
        if len(mins) < min_games:
            continue
        by_team.setdefault(team, []).append((pid, statistics.fmean(mins)))
    ranks: dict[str, int] = {}
    for members in by_team.values():
        members.sort(key=lambda kv: (-kv[1], kv[0]))
        for i, (pid, _m) in enumerate(members, start=1):
            ranks[pid] = i
    return ranks


def _curve(
    ranks: dict[str, int], means: dict[str, float]
) -> tuple[dict[int, float], dict[int, float], float, float]:
    """Measured minutes-by-rotation-rank: median per rank plus the spread within it."""
    buckets: dict[int, list[float]] = {}
    tail: list[float] = []
    for pid, rank in ranks.items():
        m = means.get(pid)
        if m is None:
            continue
        (tail if rank > MAX_TRACKED_RANK else buckets.setdefault(rank, [])).append(m)
    curve = {r: statistics.median(v) for r, v in buckets.items() if v}
    resid = {r: statistics.pvariance(v) if len(v) > 1 else 0.0 for r, v in buckets.items() if v}
    tail_mu = statistics.median(tail) if tail else min(curve.values(), default=0.0)
    tail_var = statistics.pvariance(tail) if len(tail) > 1 else max(resid.values(), default=1.0)
    # A rank with a single observation reports zero spread, which would make the role curve
    # look infinitely trustworthy. Floor it at the tail spread rather than believing that.
    resid = {r: (v if v > 0 else tail_var) for r, v in resid.items()}
    return curve, resid, tail_mu, tail_var


def _drift(
    per_player: dict[str, list[dict]], within_var: float, min_games: int
) -> tuple[float, float, int, str, str]:
    """Measure how much a player's true mean minutes moves between two periods.

    Splits each player's window in half and looks at the change in mean minutes. The raw
    variance of that change contains sampling noise from both halves; subtracting it leaves
    the *true* drift, which is what a forward projection is actually exposed to.

    Players who changed NBA team between the halves are measured separately: a trade is
    exogenous to the player's own play, so the ratio of the two drifts is a clean estimate
    of how much more uncertain a moved player's role is.
    """
    stayed: list[float] = []
    moved: list[float] = []
    noise_stayed: list[float] = []
    noise_moved: list[float] = []
    for games in per_player.values():
        half = len(games) // 2
        a, b = _minutes_of(games[:half]), _minutes_of(games[half:])
        if len(a) < min_games or len(b) < min_games:
            continue
        delta = statistics.fmean(b) - statistics.fmean(a)
        noise = within_var * (1.0 / len(a) + 1.0 / len(b))
        if games[:half][-1]["team"] != games[half:][-1]["team"]:
            moved.append(delta)
            noise_moved.append(noise)
        else:
            stayed.append(delta)
            noise_stayed.append(noise)

    def _true_var(deltas: list[float], noise: list[float]) -> float | None:
        if len(deltas) < MIN_MOVERS_FOR_DRIFT:
            return None
        return max(statistics.pvariance(deltas) - statistics.fmean(noise), 0.0)

    base = _true_var(stayed, noise_stayed)
    mover = _true_var(moved, noise_moved)
    if base is None or base <= 0.0:
        return FALLBACK_DRIFT_VAR, FALLBACK_TEAM_CHANGE_MULT, len(moved), "fallback", "fallback"
    if mover is None or mover <= 0.0:
        return base, FALLBACK_TEAM_CHANGE_MULT, len(moved), "measured", "fallback"
    return base, max(mover / base, 1.0), len(moved), "measured", "measured"


def fit_minutes(
    store, as_of: str, *, window: int = 82, min_games: int = 10
) -> MinutesFit:
    """Fit the minutes model from every game known on or before ``as_of``.

    ``window`` caps how far back each player's history reaches (in games), so a stale season
    does not outvote a recent one.
    """
    rows = store.player_game_stream_asof(as_of)
    per_player = _player_windows(rows, window)

    means: dict[str, float] = {}
    var_within: list[float] = []
    counts: list[int] = []
    for pid, games in per_player.items():
        mins = _minutes_of(games)
        if len(mins) < min_games:
            continue
        means[pid] = statistics.fmean(mins)
        var_within.append(statistics.pvariance(mins))
        counts.append(len(mins))

    if len(means) < MIN_PLAYERS_FOR_FIT:
        # Too thin to identify anything. Return a fit that is honest about it: every
        # parameter is a fallback, so projections built on it are labeled provisional.
        pool_mean = statistics.fmean(means.values()) if means else 0.0
        return MinutesFit(
            as_of=as_of, role_curve={}, role_residual_var={}, tail_minutes=pool_mean,
            tail_residual_var=FALLBACK_DRIFT_VAR, pool_mean=pool_mean,
            within_var=FALLBACK_DRIFT_VAR, between_var=FALLBACK_DRIFT_VAR,
            drift_var=FALLBACK_DRIFT_VAR, team_change_drift_mult=FALLBACK_TEAM_CHANGE_MULT,
            half_life=None, n_players=len(means), n_movers=0,
            basis={k: "fallback" for k in
                   ("role_curve", "within_var", "between_var", "drift_var", "team_change",
                    "half_life")},
        )

    pool_mean = statistics.fmean(means.values())
    within_var = statistics.fmean(var_within)
    # Player-to-player spread of *true* mean minutes: observed spread minus the sampling
    # noise already inside each player's own mean (the standard empirical-Bayes correction).
    between_var = max(
        statistics.pvariance(list(means.values())) - within_var / statistics.fmean(counts), 1e-6
    )

    ranks = _rotation_ranks(per_player, min_games)
    curve, resid, tail_mu, tail_var = _curve(ranks, means)
    drift_var, team_mult, n_movers, drift_basis, mult_basis = _drift(
        per_player, within_var, min_games
    )
    half_life, half_life_basis = _fit_half_life(per_player, min_games)

    basis = {
        "role_curve": "measured" if curve else "fallback",
        "within_var": "measured",
        "between_var": "measured",
        "drift_var": drift_basis,
        "team_change": mult_basis,
        "half_life": half_life_basis,
    }
    return MinutesFit(
        as_of=as_of, role_curve=curve, role_residual_var=resid, tail_minutes=tail_mu,
        tail_residual_var=tail_var, pool_mean=pool_mean, within_var=within_var,
        between_var=between_var, drift_var=drift_var, team_change_drift_mult=team_mult,
        half_life=half_life, n_players=len(means), n_movers=n_movers, basis=basis,
    )


# --- projecting --------------------------------------------------------------


class MinutesModel:
    """Projects per-game minutes for the upcoming season from a :class:`MinutesFit`."""

    def __init__(self, fit: MinutesFit):
        self.fit = fit

    def project(
        self,
        player_id: str,
        history: list[dict],
        *,
        stated_rank: int | None = None,
        team_changed: bool = False,
        observed_rank: int | None = None,
    ) -> MinutesProjection:
        """Project minutes from a player's own games plus, optionally, a stated forward role.

        ``history`` is that player's game stream (as-of filtered by the caller); ``stated_rank``
        is the depth-chart position recorded for the upcoming season, read as rotation rank.
        """
        f = self.fit
        mins = _minutes_of(history)
        n = len(mins)
        obs_std = statistics.pstdev(mins) if n > 1 else math.sqrt(f.within_var)
        # Recency-weighted, by the half-life that forecast best inside the training window.
        # Leaning on recent games costs sample size (n_eff ≤ n), which the shrinkage below
        # then charges for — so a role change is followed without pretending to certainty.
        obs_mean, n_eff = ew_mean(mins, f.half_life)
        if not mins:
            obs_mean = f.pool_mean

        # 1. History-only estimate: shrink the observed mean toward the pool by the ratio of
        #    game-to-game noise to player-to-player spread. With a full season this barely
        #    moves; with eight games it moves a lot, which is the point.
        if n > 0:
            precision = n_eff / f.within_var if f.within_var > 0 else 0.0
            prior_precision = 1.0 / f.between_var if f.between_var > 0 else 0.0
            hist_mean = ((obs_mean * precision + f.pool_mean * prior_precision)
                         / (precision + prior_precision)) if (precision + prior_precision) else 0.0
            hist_var = 1.0 / (precision + prior_precision) if (precision + prior_precision) else \
                f.between_var
        else:
            hist_mean, hist_var = f.pool_mean, f.between_var

        # 2. …plus the drift the player's true minutes will undergo before the target period.
        #    A team change inflates it by the measured mover/stayer ratio: the situation moved,
        #    but nothing tells us which way, so the band widens and the mean does not shift.
        drift = f.drift_var * (f.team_change_drift_mult if team_changed else 1.0)
        own_var = hist_var + drift

        role = f.role(stated_rank)
        if role is None:
            mean, var, weight = hist_mean, own_var, 0.0
        else:
            role_mu, role_var = role
            role_var = max(role_var, 1e-6)
            weight = own_var / (own_var + role_var)
            mean = (1.0 - weight) * hist_mean + weight * role_mu
            var = (own_var * role_var) / (own_var + role_var)

        # Game-to-game spread is measured on the minutes the player actually played; if the
        # projected role differs, scale it with the projected level rather than reusing it flat.
        if obs_mean > 0 and mean > 0:
            obs_std *= math.sqrt(mean / obs_mean)

        return MinutesProjection(
            player_id=player_id,
            minutes=max(mean, 0.0),
            per_game_std=max(obs_std, 0.0),
            mean_stderr=math.sqrt(max(var, 0.0)),
            observed_games=n,
            observed_minutes=hist_mean,
            rotation_rank=observed_rank,
            stated_rank=stated_rank,
            team_changed=team_changed,
            role_weight=round(weight, 4),
        )
