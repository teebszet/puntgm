"""Is the store's position data strong enough to run a positional-assignment experiment?

Task 3.14 wires positional assignment into the H₀ objective. Before measuring anything, two
properties of the *input* decide whether a result would mean anything at all, and both are
facts about our store rather than about the method:

1. **Coverage.** How many of the scored pool have a listed position? The ones that do not
   cannot be placed in a slot, and how they are handled is a thumb on the scale of the exact
   effect under test.
2. **Scarcity.** Positional assignment can only matter where a position is scarce enough that
   a position-blind drafter runs out. If every slot is comfortably over-supplied, the
   constraint never binds and a null result says nothing about the method.

The listed positions are coarse (NBA `playerindex` gives G/F/C), so `eligible_positions`
expands them and the effective system collapses to three classes — any guard fills either
guard slot, any forward fills either forward slot. That expansion is *looser* than Yahoo's
real eligibility and biases the measured effect towards zero; see `settings.py`.

Usage::

    python scripts/position_coverage.py [season ...]
"""

from __future__ import annotations

import sys
from collections import Counter

from fantasy_gm.config import Config
from fantasy_gm.data.store import Store
from fantasy_gm.draft.settings import (
    DEFAULT_SLOTS,
    POSITIONS,
    DraftSettings,
    RosterSlot,
    slot_eligibility,
)
from fantasy_gm.valuation import rosterable_pool

SEASONS = ("2023-24", "2024-25", "2025-26")
# The store holds one undated snapshot of positions; this is the date it was taken.
POSITIONS_ASOF = "2026-08-17"


def _demand(slots: list[RosterSlot], n_teams: int) -> dict[str, int]:
    """Minimum league-wide players of each fine position the slot structure forces.

    A slot that accepts several positions imposes no demand on any one of them, so only the
    slots with a single eligible position count. That makes this a *lower* bound on scarcity,
    which is the direction that keeps the conclusion honest.
    """
    out: dict[str, int] = dict.fromkeys(POSITIONS, 0)
    for slot in slots:
        if len(slot.eligible) == 1:
            out[next(iter(slot.eligible))] += n_teams
    return out


def main(seasons: tuple[str, ...] = SEASONS) -> None:
    store = Store(Config().db_path)
    positions = store.player_positions_asof(POSITIONS_ASOF)
    settings = DraftSettings()
    slots = [RosterSlot.of(s) for s in DEFAULT_SLOTS]
    demand = _demand(slots, settings.n_teams)
    pool_size = settings.n_teams * settings.n_rounds

    print(f"store positions: {len(positions)} (as of {POSITIONS_ASOF}, one snapshot, no history)")
    print(f"pool: top {pool_size} by rosterable value; slots: {' '.join(DEFAULT_SLOTS)}")
    print(f"forced demand across {settings.n_teams} teams: "
          + "  ".join(f"{p}={demand[p]}" for p in POSITIONS))
    print()
    header = f"{'season':<9} {'pool':>5} {'listed':>7} {'unlisted':>9} " + " ".join(
        f"{p:>6}" for p in POSITIONS
    ) + "   binding"
    print(header)

    for season in seasons:
        pool = [
            p if isinstance(p, str) else p.player_id
            for p in rosterable_pool(store, season, pool_size=pool_size)
        ]
        eligible, unlisted = slot_eligibility(positions, pool)
        supply: Counter[str] = Counter()
        for fine in eligible.values():
            for p in fine:
                supply[p] += 1
        # Which position is closest to running out, as a share of forced demand.
        ratios = {p: supply[p] / demand[p] for p in POSITIONS if demand[p]}
        tightest = min(ratios, key=lambda p: ratios[p])
        print(
            f"{season:<9} {len(pool):>5} {len(eligible):>7} {len(unlisted):>9} "
            + " ".join(f"{supply[p]:>6}" for p in POSITIONS)
            + f"   {tightest} {supply[tightest]}/{demand[tightest]} ({ratios[tightest]:.2f}x)"
        )


if __name__ == "__main__":
    main(tuple(sys.argv[1:]) or SEASONS)
