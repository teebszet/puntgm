"""Scoring window derived from a league's lineup cadence (D9).

* ``weekly-lock``  -> the current Monday–Sunday period containing ``as_of``.
* ``daily-change`` -> a single day (``as_of``), evaluated at daily granularity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from fantasy_gm.config import CADENCE_DAILY, CADENCE_WEEKLY


@dataclass(frozen=True)
class ScoringWindow:
    start: str  # ISO date, inclusive
    end: str  # ISO date, inclusive
    cadence: str

    @property
    def label(self) -> str:
        return "day" if self.cadence == CADENCE_DAILY else "week"


def window_for(cadence: str, as_of: str) -> ScoringWindow:
    d = date.fromisoformat(as_of)
    if cadence == CADENCE_DAILY:
        return ScoringWindow(as_of, as_of, cadence)
    if cadence == CADENCE_WEEKLY:
        monday = d - timedelta(days=d.weekday())
        sunday = monday + timedelta(days=6)
        return ScoringWindow(monday.isoformat(), sunday.isoformat(), cadence)
    raise ValueError(f"unknown lineup cadence {cadence!r}")
