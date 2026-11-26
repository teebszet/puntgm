"""Per-category production, conditioned on projected minutes.

The projection is deliberately *not* a carry-forward of last season's per-game line. It is a
**per-minute rate** times projected minutes, which is what makes the model react to role: a
player who moves from 22 minutes to 32 keeps their rate and gains production, and a player
who loses a starting job loses it, without either being asserted anywhere.

Rates are shrunk toward the pool by empirical Bayes, with the prior taken from the player's
*rotation tier* rather than the whole league — a lead guard's per-minute usage is nothing
like a twelfth man's, and shrinking toward a league-wide average would drag every star down
and every bench player up. The shrinkage weight is the ratio of a player's own sampling
error to the measured player-to-player spread, so it is fit, not tuned.

Two distinct uncertainties come out (requirement: they are separate terms, A-DRAFT-2):

* ``per_game_std`` — game-to-game production spread. Measured on the games the player
  actually played, then rescaled to the projected minutes level by a **measured** exponent
  (regressing log σ on log minutes across the pool) rather than assumed to scale linearly.
* ``mean_stderr`` — how well the projected mean itself is known. By the delta method on
  ``rate × minutes`` this carries *both* the rate's estimation error and the minutes model's,
  which is why a player with a settled role and a long history comes out tight and a
  role-changer with 15 games comes out wide even when their means match.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

# Rotation-tier boundaries for the rate prior: leads, second unit, rotation, fringe. Tiers
# exist because per-minute usage is strongly role-dependent; the *rates* inside them are
# measured, only the bucketing is structural.
TIER_BOUNDS = (3, 6, 9)
MIN_PLAYERS_FOR_FIT = 20
MIN_TIER_PLAYERS = 5

FALLBACK_EXPONENT = 0.5  # Poisson-like: σ ∝ √minutes. Used only when the regression fails.


def tier_of(rank: int | None) -> int:
    """Rotation tier (0 = leads … 3 = fringe) for a rotation rank."""
    if rank is None:
        return len(TIER_BOUNDS)
    for i, bound in enumerate(TIER_BOUNDS):
        if rank <= bound:
            return i
    return len(TIER_BOUNDS)


@dataclass(frozen=True)
class RatesFit:
    """Measured per-minute rate priors and variance scaling, as of a date."""

    as_of: str
    keys: tuple[str, ...]
    tier_rate: dict[tuple[str, int], float]   # (stat key, tier) -> pool per-minute rate
    pool_rate: dict[str, float]               # (stat key) -> pool per-minute rate
    between_var: dict[str, float]             # player-to-player spread of true per-minute rate
    std_exponent: dict[str, float]            # σ ∝ minutes ** exponent (measured)
    tier_std: dict[tuple[str, int], float]    # (stat key, tier) -> median per-game σ
    n_players: int
    basis: dict[str, str] = field(default_factory=dict)

    def prior_rate(self, key: str, tier: int) -> float:
        return self.tier_rate.get((key, tier), self.pool_rate.get(key, 0.0))


@dataclass(frozen=True)
class RateProjection:
    key: str
    per_game_mean: float
    per_game_std: float
    mean_stderr: float
    per_minute_rate: float


def _totals(games: list[dict], keys: tuple[str, ...]) -> tuple[dict[str, float], float, int]:
    """(stat totals, total minutes, games) over games with recorded minutes."""
    totals = dict.fromkeys(keys, 0.0)
    minutes = 0.0
    n = 0
    for g in games:
        if g["minutes"] is None:
            continue
        n += 1
        minutes += g["minutes"]
        for k in keys:
            totals[k] += float(g["stats"].get(k, 0.0))
    return totals, minutes, n


def _per_game_std(games: list[dict], key: str) -> float:
    vals = [float(g["stats"].get(key, 0.0)) for g in games if g["minutes"] is not None]
    return statistics.pstdev(vals) if len(vals) > 1 else 0.0


def _regress_slope(xs: list[float], ys: list[float]) -> float | None:
    """Least-squares slope of y on x, or None if x has no spread."""
    if len(xs) < MIN_PLAYERS_FOR_FIT:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / sxx


def fit_rates(
    store,
    as_of: str,
    keys: list[str],
    ranks: dict[str, int],
    *,
    window: int = 82,
    min_games: int = 10,
) -> RatesFit:
    """Fit per-minute rate priors and variance scaling from games known on or before ``as_of``.

    ``ranks`` maps player id to rotation rank (from the minutes fit), which is what tiers the
    rate priors.
    """
    from fantasy_gm.projections.minutes import _player_windows

    keys_t = tuple(keys)
    per_player = _player_windows(store.player_game_stream_asof(as_of), window)

    samples: list[tuple[str, int, dict[str, float], float, int]] = []
    for pid, games in per_player.items():
        totals, minutes, n = _totals(games, keys_t)
        if n < min_games or minutes <= 0:
            continue
        samples.append((pid, tier_of(ranks.get(pid)), totals, minutes, n))

    tier_rate: dict[tuple[str, int], float] = {}
    pool_rate: dict[str, float] = {}
    between_var: dict[str, float] = {}
    std_exponent: dict[str, float] = {}
    tier_std: dict[tuple[str, int], float] = {}

    if not samples:
        return RatesFit(as_of, keys_t, {}, dict.fromkeys(keys_t, 0.0),
                        dict.fromkeys(keys_t, 0.0), dict.fromkeys(keys_t, FALLBACK_EXPONENT),
                        {}, 0, {k: "fallback" for k in keys_t})

    tiers = sorted({t for _p, t, _tot, _m, _n in samples})
    total_minutes = sum(m for _p, _t, _tot, m, _n in samples)
    basis: dict[str, str] = {}

    for key in keys_t:
        # Pool and tier priors are *minutes-weighted* (Σstat / Σminutes), not an average of
        # per-player rates — otherwise a 6-minute-a-night player counts as much as a starter.
        pool_rate[key] = sum(tot[key] for _p, _t, tot, _m, _n in samples) / total_minutes
        for t in tiers:
            members = [(tot, m) for _p, tt, tot, m, _n in samples if tt == t]
            mins = sum(m for _tot, m in members)
            if len(members) >= MIN_TIER_PLAYERS and mins > 0:
                tier_rate[(key, t)] = sum(tot[key] for tot, _m in members) / mins

        # Player-to-player spread of the *true* rate: observed spread minus each player's own
        # sampling error (empirical Bayes), so shrinkage is not fit to noise.
        rates, samp_vars, log_m, log_s, stds_by_tier = [], [], [], [], {}
        for pid, t, tot, minutes, n in samples:
            mean_minutes = minutes / n
            r = tot[key] / minutes
            sd = _per_game_std(per_player[pid], key)
            rates.append(r)
            if mean_minutes > 0:
                samp_vars.append((sd ** 2) / max(n, 1) / (mean_minutes ** 2))
            stds_by_tier.setdefault(t, []).append(sd)
            if sd > 0 and mean_minutes > 0:
                log_m.append(math.log(mean_minutes))
                log_s.append(math.log(sd))
        obs_var = statistics.pvariance(rates) if len(rates) > 1 else 0.0
        noise = statistics.fmean(samp_vars) if samp_vars else 0.0
        between_var[key] = max(obs_var - noise, obs_var * 1e-3, 1e-12)

        slope = _regress_slope(log_m, log_s)
        std_exponent[key] = min(max(slope, 0.0), 2.0) if slope is not None else FALLBACK_EXPONENT
        basis[f"{key}.std_exponent"] = "measured" if slope is not None else "fallback"
        for t, v in stds_by_tier.items():
            tier_std[(key, t)] = statistics.median(v)

    return RatesFit(as_of, keys_t, tier_rate, pool_rate, between_var, std_exponent,
                    tier_std, len(samples), basis)


class RatesModel:
    """Projects per-game category production from a :class:`RatesFit` and projected minutes."""

    def __init__(self, fit: RatesFit):
        self.fit = fit

    def project(
        self,
        key: str,
        history: list[dict],
        *,
        projected_minutes: float,
        minutes_stderr: float,
        tier: int,
    ) -> RateProjection:
        f = self.fit
        totals, minutes, n = _totals(history, (key,))
        prior = f.prior_rate(key, tier)
        between = f.between_var.get(key, 0.0)

        if n == 0 or minutes <= 0:
            # No usable history: the tier prior is the whole estimate, and its uncertainty is
            # the player-to-player spread within the tier — deliberately wide.
            rate, rate_var = prior, between
            obs_std = f.tier_std.get((key, tier), 0.0)
            obs_mean_minutes = 0.0
        else:
            obs_mean_minutes = minutes / n
            rate = totals[key] / minutes
            sd = _per_game_std(history, key)
            samp_var = (sd ** 2) / n / (obs_mean_minutes ** 2) if obs_mean_minutes > 0 else between
            samp_var = max(samp_var, 1e-12)
            if between > 0:
                w = (1.0 / samp_var) / (1.0 / samp_var + 1.0 / between)
                rate = w * rate + (1.0 - w) * prior
                rate_var = 1.0 / (1.0 / samp_var + 1.0 / between)
            else:
                rate_var = samp_var
            obs_std = sd

        mean = rate * projected_minutes

        # Game-to-game spread, rescaled from the minutes the player actually played to the
        # minutes projected, by the measured exponent (σ ∝ minutes**β).
        exponent = f.std_exponent.get(key, FALLBACK_EXPONENT)
        if obs_std > 0 and obs_mean_minutes > 0 and projected_minutes > 0:
            std = obs_std * (projected_minutes / obs_mean_minutes) ** exponent
        elif obs_std > 0:
            std = obs_std
        else:
            std = f.tier_std.get((key, tier), 0.0)

        # Delta method on rate × minutes: both estimates are uncertain, and the product's
        # error carries both. This is the band that is *not* game-to-game noise.
        stderr = math.sqrt(
            (projected_minutes ** 2) * rate_var + (rate ** 2) * (minutes_stderr ** 2)
        )
        return RateProjection(key, max(mean, 0.0), max(std, 0.0), stderr, rate)
