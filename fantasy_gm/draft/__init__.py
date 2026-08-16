"""Draft-time valuation and pick selection (Track A).

The market values players in season-long z-score space. That is provably the wrong metric
for weekly H2H, because z-score is the special case of a more general metric under the
assumption that future production is known exactly (Rosenof, arXiv 2307.02188). What
actually decides a category is beating one opponent over ~4 games, so period-to-period
variance is first-order.

This package builds that correction in two layers:

* :mod:`fantasy_gm.draft.xscore` — the variance-aware standardisation basis, and its static
  reduction (**G-score**), which is a complete draft board on its own.
* (next) the dynamic **H₀** optimizer, which re-solves at every pick against the roster
  already drafted rather than reading down a fixed list.

The existing ``fantasy_gm.valuation`` z-score is deliberately left untouched — it is the
labeled baseline the replay harness has to beat.
"""

from fantasy_gm.draft.xscore import (
    CategoryBasis,
    PeriodStats,
    VarianceMode,
    g_score_board,
    g_scores,
    measure_period_stats,
    xscore_basis,
)

__all__ = [
    "CategoryBasis",
    "PeriodStats",
    "VarianceMode",
    "g_score_board",
    "g_scores",
    "measure_period_stats",
    "xscore_basis",
]
