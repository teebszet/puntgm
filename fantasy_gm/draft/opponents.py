"""Modelling the other eleven people in the draft room (design D10).

Two things follow from knowing how others draft:

* **Scarcity** — what will still be there when your turn comes round again. This is what
  separates "best player available" from "best player who will not survive the wheel".
* **A field to draft against** — the replay harness needs opponents that behave like drafters
  rather than like a random pool.

Opponents pick from an ADP ordering with bounded noise. That matches the published simulations
(making our numbers comparable) and needs no per-league setup. Modelling *specific* leaguemates'
tendencies would be higher value in one particular draft and worth nothing in general, so it is
deliberately out of scope.

**ADP source.** Yahoo's `draft_analysis` was the intended feed, but the Fantasy API is now
application-gated with manual review, so it cannot be relied on for replay. `derive_adp_order`
falls back to the store's own value ranking — the same proxy `simulate.py` already uses for its
snake drafts. Real market ADP differs from a value ranking in exactly the interesting way (the
market is wrong, which is where value comes from), so replay numbers built on the proxy should
be read as a lower bound on the edge, not an estimate of it.
"""

from __future__ import annotations

import math
import random


def derive_adp_order(store, season: str) -> list[str]:
    """An ADP stand-in: players ordered by data-derived 9-cat z-value.

    Reuses `simulate._adp_order` so replay drafts and simulated leagues share one notion of
    "what the market thinks", rather than quietly diverging.
    """
    from fantasy_gm.data.simulate import _adp_order

    return _adp_order(store, season)


def adp_ranks(order: list[str]) -> dict[str, float]:
    """``{player_id: expected pick number}`` (1-indexed) from an ordering."""
    return {pid: float(i + 1) for i, pid in enumerate(order)}


def survival_probability(
    rank: float | None, picks_until_next: int, noise_sd: float = 6.0
) -> float:
    """P(a player is still available at our next pick).

    A player's actual draft slot is modelled as their ADP rank plus normal noise, so the
    question "will they last?" becomes "is their realized slot beyond the picks that happen
    before our next turn?". ``rank=None`` (nobody has an ADP) is treated as certain survival —
    an unranked player is not being targeted by anyone.
    """
    if rank is None:
        return 1.0
    if picks_until_next <= 0:
        return 1.0
    sd = max(noise_sd, 1e-6)
    z = (rank - picks_until_next) / sd
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


class AdpBot:
    """An opponent that drafts near ADP, reaching or sliding a little.

    ``reach`` bounds how far from the top of the remaining board it will pick. Deterministic
    given the seeded ``rng``, so replays reproduce exactly.
    """

    name = "adp"

    def __init__(self, order: list[str], rng: random.Random, reach: int = 3):
        self._rank = {pid: i for i, pid in enumerate(order)}
        self._rng = rng
        self._reach = max(1, reach)

    def pick(self, available: list[str]) -> str | None:
        if not available:
            return None
        ranked = sorted(available, key=lambda p: (self._rank.get(p, 10**9), p))
        return self._rng.choice(ranked[: min(self._reach, len(ranked))])


def picks_between(seat: int, n_teams: int, round_index: int) -> int:
    """Picks made by others between this seat's pick in ``round_index`` and its next one.

    In a snake draft the gap alternates: a seat near the turn waits only a couple of picks,
    while a seat at the wheel waits almost two full rounds. That asymmetry is the whole reason
    survival probability matters more at some seats than others.
    """
    if round_index % 2 == 0:                 # forward round
        return 2 * (n_teams - seat) - 1
    return 2 * seat + 1
