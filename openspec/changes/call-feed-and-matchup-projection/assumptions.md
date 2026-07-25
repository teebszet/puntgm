# Assumptions ledger

Every mathematical assumption currently baked into projection/feed, whether it is *asserted*
(from intuition, not yet measured) or *known-wrong*, the exact statistic that would validate it,
and the data needed. **Principle: nothing asserted here should stay a hard-coded constant once
real data exists — each becomes a measured parameter, or is replaced.**

Real validation requires the `nba_api` backfill (real box scores). The synthetic season cannot
validate these — its numbers are generated from these same assumptions, so testing against it is
circular. Synthetic data validates the *mechanism*, not the *claim*.

Status legend: **ASSERTED** (intuition, unmeasured) · **KNOWN-WRONG** (mathematically incorrect,
fix regardless) · **HEURISTIC** (tunable knob, calibrate).

---

## A1. Category variance grouping — ASSERTED  ⟵ the one you flagged

**Claim:** pts/reb are low-variance; stl/blk/ast are high-variance; fg3m/tov medium; ft_pct high.
Encoded in `config.CATEGORY_VARIANCE_LEVEL`.

**Validate:** for each category, compute per-player **coefficient of variation** CV = σ/μ of
per-game production across a season, then take the league median CV per category and rank them.
Confirm (or refute) that stl/blk/ast sit above pts/reb.
**Data:** real per-player game logs (one season is enough; three strengthens it).

**Important nuance:** the projector *already* measures each player's own per-game variance from
their logs (`store.player_distribution`). So if A1 is true, it is **already captured** by the
measured variances — the extra `VARIANCE_MULTIPLIER` (A2) is then double-counting. The principled
outcome of validating A1 may be to **delete the multiplier**, not to tune it.

## A2. Variance multiplier magnitudes — ASSERTED / likely redundant

**Claim:** `VARIANCE_MULTIPLIER = {low: 0.6, medium: 1.0, high: 1.6}` — a hand-set fudge on top of
measured std.
**Validate:** calibration (A7). If measured per-player variance already reproduces observed
category volatility, the multiplier should be ~1.0 everywhere and can be dropped. Only keep a
correction if calibration is systematically off in a category-dependent way.
**Data:** replayed real matchups.

## A3. Normal approximation for win probability — ASSERTED, weak for low-count cats

**Claim:** the difference of the two teams' category totals is ~Normal, so
`win_prob = Φ(diff / combined_std)` (`engine/projection.py`).
**Why suspect:** blocks/steals are low-count per game (μ≈1), so a few-game sum is closer to
**Poisson / negative-binomial** than Normal — the tails (and thus "safe/gone" calls) are
mis-estimated exactly for the high-variance cats that matter most.
**Validate:** bootstrap the actual end-of-period category total by resampling each player's real
per-game lines over the remaining schedule (Monte Carlo), and compare the empirical win
probability to the Normal-approx one. Divergence → replace Normal with the empirical/bootstrap
distribution (or a Poisson model for count cats).
**Data:** real per-game logs.

## A4. Games are independent (variance adds linearly) — ASSERTED

**Claim:** `Var(sum) = Σ rg·σ²` assumes each remaining game is i.i.d.
**Reality risk:** back-to-backs, blowouts (starters rest), and role changes induce correlation.
**Validate:** measure lag-1 autocorrelation of per-game category production; check whether the
variance of k-game sums actually scales ∝ k (vs. sub-/super-linear).
**Data:** real per-game logs with dates (to identify B2Bs).

## A5. Expected per-game = trailing N-game mean (N=10) — HEURISTIC

**Claim:** `config.recent_games_window = 10`, unweighted mean predicts remaining games.
**Validate:** backtest predictive error (MAE) of trailing-N mean vs. actual next-M games across
N ∈ {5,8,10,15, full-season} and vs. exponentially-weighted; pick the minimum-error estimator.
**Data:** real per-game logs.

## A6. Fantasy-value proxy weights — ASSERTED

**Claim:** `store._fantasy_points` uses pts×1, reb×1.2, ast×1.5, stl×3, blk×3, fg3m×1, tov×−1.
Used for ADP ordering and drop selection.
**Validate:** replace with **per-category z-scores** (Basketball-Monster style: standardize each
category by its league σ). The z-score *is* the measured value; validate by whether z-score
rankings predict category-winning contribution better than the ad-hoc weights.
**Data:** league-wide per-player per-game distributions.

## A7. safe / contested / gone thresholds (0.80 / 0.20) — HEURISTIC, calibrate

**Claim:** `SAFE_PROB=0.80`, `GONE_PROB=0.20`.
**Validate:** **calibration curve** — of all cats the model called p≈0.8, did ≈80% actually win?
Bin predicted win-prob vs. realized win rate over many replayed matchups; the label thresholds
follow from the precision you want. `engine.scoring.calibration` already computes the "safe held"
count — extend it to a full reliability diagram.
**Data:** replayed real matchups.

## A8. Percentage categories summed — KNOWN-WRONG (fix regardless)

**Claim:** fg_pct/ft_pct are currently *summed* per game like counting stats.
**Reality:** percentages must be **volume-weighted** — aggregate makes/attempts, then divide
(ΣFGM/ΣFGA). A 90% FT shooter on 2 attempts ≠ on 10. This is not an assumption to validate; it's
a correctness bug. Fix: track makes+attempts and compute the ratio; project attempts too.

## A9. Replacement availability by category — ASSERTED (your "backup PG for assists" point)

**Claim:** some contested cats are more *actionable* because the wire is deep in them (assists via
backup PGs).
**Validate:** for each category, measure the distribution of the best-available (unrostered)
per-game production across the season — e.g. the Nth-best FA's per-game in that cat. Deeper supply
= more actionable. Feeds whether a contested cat is worth chasing.
**Data:** real logs + realistic roster/availability (simulated leagues, then your imported leagues).

## A10. Signal strength formula & thresholds — HEURISTIC

**Claim:** `strength = confidence × impact × relevance`; `STRONG_STRENGTH=0.45`; confidence weights
0.3 (base) / 0.3 (sustained) / 0.25 (causal).
**Validate:** forward test — do signals graded "strong" actually precede sustained production
gains more than "soft" ones? Measure signal → forward-N-game production lift; tune the bar so
"strong" has meaningfully higher precision.
**Data:** real usage/role + forward production.

## A11. Season-stage boundaries & weights — HEURISTIC

**Claim:** early ≤0.30, late ≥0.70 of season; relevance ×1.3 early / ×0.8 late for usage signals.
**Validate:** measure when usage/roles actually stabilize (rolling variance of team minutes
distributions over the season) to set "early"; measure ROS-pickup payoff vs. streamer payoff by
week to justify the tilt.
**Data:** real usage/role time series.

---

## Proposed handling

1. **Mark provisional now:** the specs and code label A1–A2, A6, A10–A11 as provisional pending
   validation; the `matchup-projection` spec's variance requirement is softened from asserting the
   specific grouping to requiring an **empirically-measured** per-category variance profile.
2. **Build a validation/calibration harness** (`fantasy_gm/validation/`) that computes A1, A3–A5,
   A7, A9 from backfilled data and emits a measured-parameter profile the projector loads — so the
   constants become *derived*, not asserted.
3. **Fix A8** (percentage cats) regardless — it's a correctness bug, not a knob.
4. Run the harness on the real `nba_api` backfill (networked machine) and replace each provisional
   constant with its measured value; keep the reliability diagram (A7) as an ongoing check.
