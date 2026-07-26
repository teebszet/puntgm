"""Validation & calibration harness (D10).

Turns asserted constants into *measured* parameters. Run over a real backfilled season to
replace provisional assumptions (see ``openspec`` assumptions ledger). NOTE: run against real
``nba_api`` data — the synthetic season is generated from the same assumptions and cannot
validate them; on synthetic data these functions only prove the mechanism works.
"""

from fantasy_gm.validation.measure import (
    bootstrap_category_winprob,
    derive_variance_profile,
    measure_autocorrelation,
    measure_category_cv,
)

__all__ = [
    "measure_category_cv",
    "measure_autocorrelation",
    "derive_variance_profile",
    "bootstrap_category_winprob",
]
