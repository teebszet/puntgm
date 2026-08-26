"""League format as configuration (design D12).

Everything the optimizer needs to know about the format lives here, so changing the category
set, roster shape, or objective is a config change rather than a code change. Roto and auction
are out of scope for v1, but the objective is a seam rather than a hardcode: roto needs a
different objective function, not a different optimizer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from fantasy_gm.config import DEFAULT_CATEGORIES

# Standard Yahoo 9-cat position set.
POSITIONS = ("PG", "SG", "SF", "PF", "C")
_FLEX = {
    "G": ("PG", "SG"),
    "F": ("SF", "PF"),
    "UTIL": POSITIONS,
}

# The slot vocabulary above is *fine* (PG/SG/SF/PF/C). The only position source this project
# has is NBA `playerindex`, which is *coarse*: it lists G, F, C and hyphenated pairs, and
# `PlayerPosition.slots()` hands those through unchanged. The two vocabularies do not
# intersect, so before this map existed `RosterSlot.accepts` returned False for every guard
# and every forward against every slot — `assign_to_slots` placed centres and nobody else.
# `assignment.py` was not merely uncalled; as wired to our store it would have placed nobody.
#
# **This expansion is deliberately looser than the format it models.** Yahoo lists true
# PG/SG/SF/PF eligibility per player; we let any listed guard fill either guard slot. That
# makes the positional constraint weaker than a real league's, which biases any measured
# effect of positional assignment *towards zero*. A null result here is therefore not
# evidence that positional assignment does not matter — it is bounded below by the coarseness
# of the input. Real eligibility arrives with the Yahoo API (A-DRAFT gate, GER-5).
_LISTED_TO_FINE: dict[str, tuple[str, ...]] = {
    "PG": ("PG",),
    "SG": ("SG",),
    "SF": ("SF",),
    "PF": ("PF",),
    "C": ("C",),
    "G": ("PG", "SG"),
    "F": ("SF", "PF"),
}


def eligible_positions(listed: Iterable[str]) -> frozenset[str]:
    """Expand listed position tokens into the fine positions they may fill.

    ``("G", "F")`` -> ``{"PG", "SG", "SF", "PF"}``. Tokens outside the map contribute nothing
    rather than raising: a source that invents a label should cost us that label, not the
    player. An empty result means "this listing tells us nothing" and is the caller's problem
    to handle — see :func:`slot_eligibility`, which surfaces it rather than defaulting.
    """
    out: set[str] = set()
    for token in listed:
        out.update(_LISTED_TO_FINE.get(token.strip().upper(), ()))
    return frozenset(out)


def slot_eligibility(
    positions: Mapping[str, object], players: Iterable[str]
) -> tuple[dict[str, frozenset[str]], list[str]]:
    """Fine-position sets for ``players``, plus the ones the store cannot place.

    ``positions`` is :meth:`Store.player_positions_asof`'s mapping — anything exposing
    ``.slots()`` per player works. Returns ``({player_id: fine_positions}, unlisted)``.

    **``unlisted`` is returned rather than defaulted on purpose.** Roughly 5-9 of a 156-man
    scored pool have no listed position in the store. Silently treating them as ineligible
    would delete real players from the lineup; silently treating them as UTIL-eligible would
    hand them a flexibility the listed players do not get. Either choice is a thumb on the
    scale of the exact experiment this feeds, and the last time a pool-membership mismatch
    went unchecked here (`derive_adp_order`, task 3.16) it was worth more than the effect
    under test. The caller decides, in the open.
    """
    eligible: dict[str, frozenset[str]] = {}
    unlisted: list[str] = []
    for pid in players:
        rec = positions.get(pid)
        fine = eligible_positions(rec.slots()) if rec is not None else frozenset()
        if fine:
            eligible[pid] = fine
        else:
            unlisted.append(pid)
    return eligible, unlisted


class Objective(StrEnum):
    """What a manager is actually trying to maximise.

    The two are genuinely different optimizations: EACH_CATEGORY rewards piling up category
    wins wherever they are cheapest, while MOST_CATEGORIES rewards clearing a majority and is
    indifferent to the margin beyond that — which is what makes conceding categories rational.
    """

    EACH_CATEGORY = "each_category"    # maximise Σ_c P(win c)
    MOST_CATEGORIES = "most_categories"  # maximise P(win ≥ ⌈C/2⌉)


@dataclass(frozen=True)
class RosterSlot:
    """One startable roster spot and the positions that may fill it."""

    name: str
    eligible: frozenset[str]

    @classmethod
    def of(cls, name: str) -> RosterSlot:
        if name in _FLEX:
            return cls(name, frozenset(_FLEX[name]))
        if name in POSITIONS:
            return cls(name, frozenset({name}))
        if name in ("BN", "BENCH"):
            return cls(name, frozenset(POSITIONS))
        raise ValueError(f"unknown roster slot: {name}")

    def accepts(self, positions: frozenset[str]) -> bool:
        return bool(self.eligible & positions)


# Yahoo's default 9-cat shape: PG SG G SF PF F C C UTIL UTIL + 3 bench.
DEFAULT_SLOTS = ("PG", "SG", "G", "SF", "PF", "F", "C", "C", "UTIL", "UTIL", "BN", "BN", "BN")


@dataclass(frozen=True)
class DraftSettings:
    """Format parameters. ``rounds`` defaults to the roster size, which is what a snake draft
    fills."""

    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
    slots: list[RosterSlot] = field(
        default_factory=lambda: [RosterSlot.of(s) for s in DEFAULT_SLOTS]
    )
    n_teams: int = 12
    objective: Objective = Objective.MOST_CATEGORIES
    rounds: int | None = None

    @property
    def n_rounds(self) -> int:
        return self.rounds if self.rounds is not None else len(self.slots)

    @property
    def n_categories(self) -> int:
        return len(self.categories)

    @property
    def majority(self) -> int:
        """Categories needed to win the matchup."""
        return self.n_categories // 2 + 1

    @property
    def starting_slots(self) -> list[RosterSlot]:
        return [s for s in self.slots if s.name not in ("BN", "BENCH")]

    @classmethod
    def from_names(
        cls,
        slot_names: list[str],
        categories: list[str] | None = None,
        n_teams: int = 12,
        objective: Objective = Objective.MOST_CATEGORIES,
    ) -> DraftSettings:
        return cls(
            categories=list(categories or DEFAULT_CATEGORIES),
            slots=[RosterSlot.of(s) for s in slot_names],
            n_teams=n_teams,
            objective=objective,
        )
