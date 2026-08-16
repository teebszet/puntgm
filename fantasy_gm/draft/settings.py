"""League format as configuration (design D12).

Everything the optimizer needs to know about the format lives here, so changing the category
set, roster shape, or objective is a config change rather than a code change. Roto and auction
are out of scope for v1, but the objective is a seam rather than a hardcode: roto needs a
different objective function, not a different optimizer.
"""

from __future__ import annotations

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
