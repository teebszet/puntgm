"""Players with no NBA history — an explicit, labeled prior, not a model (design D9).

A store of NBA box scores cannot project a player who has never played an NBA game. That is
a structural gap, not a modeling choice, so rookies get their own path and every projection
that comes out of it is stamped ``ProjectionBasis.PRIOR`` — nothing asserted is allowed to
masquerade as something measured (project standing rule).

The prior is expressed in the same currency as the rest of the model: a **rotation rank**.
That matters, because rank is the one thing the store *can* measure — the minutes curve and
the per-minute rate tiers are both fit from real games. So a rookie projection is
"draft slot → expected rotation rank → measured minutes and measured tier rates", and the
only asserted link in that chain is the first arrow.

Two ways that arrow gets set:

* **Fitted** (preferred) — from past ``incoming_players`` cohorts whose realized rookie
  seasons are in the store: the median rotation rank actually reached, by draft-slot bucket.
* **Fallback** — the table below, used when the store holds no past cohort. It is recorded
  as ``basis="fallback"`` on every projection it touches, and A-DRAFT-6 in the assumptions
  ledger carries it as unmeasured.

Uncertainty is wide on purpose: the band is the measured spread of minutes *among players at
that rank*, inflated by the team-change drift term, because a rookie is by construction in a
situation nobody has observed them in. If the band swamps the signal, that is the honest
answer and the optimizer should price it, not have it hidden.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

# Draft-slot buckets, as (inclusive upper bound, label). Undrafted/unknown falls through.
SLOT_BUCKETS: tuple[tuple[int, str], ...] = ((5, "1-5"), (14, "6-14"), (30, "15-30"),
                                             (60, "31-60"))
UNDRAFTED = "undrafted"

# ASSERTED (A-DRAFT-6): expected rotation rank by draft-slot bucket, used only when no past
# rookie cohort is in the store to fit against. Deliberately conservative — rookies rarely
# lead a rotation — and deliberately coarse, because the underlying sample per slot is small.
FALLBACK_SLOT_RANK: dict[str, int] = {
    "1-5": 6, "6-14": 8, "15-30": 10, "31-60": 12, UNDRAFTED: 13,
}

MIN_COHORT_FOR_FIT = 8   # per bucket, below which the bucket keeps the fallback rank


def slot_bucket(draft_pick: int | None) -> str:
    if draft_pick is None or draft_pick <= 0:
        return UNDRAFTED
    for bound, label in SLOT_BUCKETS:
        if draft_pick <= bound:
            return label
    return UNDRAFTED


@dataclass(frozen=True)
class RookiePrior:
    """Expected rotation rank by draft-slot bucket, plus where each bucket's number came from."""

    slot_rank: dict[str, int]
    basis: dict[str, str] = field(default_factory=dict)   # bucket -> "fitted" | "fallback"
    cohort_size: dict[str, int] = field(default_factory=dict)

    def rank_for(self, draft_pick: int | None) -> tuple[int, str, str]:
        """(expected rotation rank, bucket label, basis) for a draft slot."""
        bucket = slot_bucket(draft_pick)
        return (self.slot_rank.get(bucket, FALLBACK_SLOT_RANK[bucket]), bucket,
                self.basis.get(bucket, "fallback"))

    @property
    def is_fitted(self) -> bool:
        return any(v == "fitted" for v in self.basis.values())


def fit_rookie_prior(store, season: str, as_of: str, ranks: dict[str, int]) -> RookiePrior:
    """Fit expected rookie rotation rank by draft slot from past incoming-player cohorts.

    ``ranks`` is the measured rotation rank of every player with history (from the minutes
    fit). A past rookie who is now in that map contributes their realized rank to the bucket
    they were drafted in. Buckets without enough of a cohort keep the fallback and say so.
    """
    cohorts: dict[str, list[int]] = {}
    for row in store.conn.execute(
        """SELECT player_id, draft_pick FROM incoming_players
           WHERE season < ? AND known_from <= ?""",
        (season, as_of),
    ):
        rank = ranks.get(row["player_id"])
        if rank is not None:
            cohorts.setdefault(slot_bucket(row["draft_pick"]), []).append(rank)

    slot_rank = dict(FALLBACK_SLOT_RANK)
    basis = dict.fromkeys(FALLBACK_SLOT_RANK, "fallback")
    sizes: dict[str, int] = {}
    for bucket, observed in cohorts.items():
        sizes[bucket] = len(observed)
        if len(observed) >= MIN_COHORT_FOR_FIT:
            slot_rank[bucket] = int(round(statistics.median(observed)))
            basis[bucket] = "fitted"
    return RookiePrior(slot_rank, basis, sizes)


@dataclass(frozen=True)
class RookieMinutes:
    """A rookie's projected minutes: measured curve, asserted (or fitted) entry point."""

    minutes: float
    per_game_std: float
    mean_stderr: float
    rank: int
    bucket: str
    basis: str          # "fitted" | "fallback" — where the slot→rank arrow came from


def project_rookie_minutes(prior: RookiePrior, minutes_fit, draft_pick: int | None
                           ) -> RookieMinutes:
    """Map a draft slot onto the measured minutes-by-rotation-rank curve.

    The band combines the spread of minutes *within* that rank with the team-change drift
    term, because a rookie's situation is one nobody has observed them in.
    """
    rank, bucket, basis = prior.rank_for(draft_pick)
    role = minutes_fit.role(rank)
    if role is None:
        minutes, role_var = minutes_fit.pool_mean, minutes_fit.between_var
    else:
        minutes, role_var = role
    var = role_var + minutes_fit.drift_var * minutes_fit.team_change_drift_mult
    # No games have been played, so game-to-game spread has to come from the pool: use the
    # same √minutes shape the rates model measures, anchored on the pool's own spread.
    per_game_std = math.sqrt(minutes_fit.within_var) * math.sqrt(
        minutes / minutes_fit.pool_mean) if minutes_fit.pool_mean > 0 else 0.0
    return RookieMinutes(minutes, per_game_std, math.sqrt(var), rank, bucket, basis)
