# Assumptions ledger — draft engine

Every mathematical assumption inherited from the `H₀` literature or introduced here, whether it is
**inherited** (stated by the paper, adopted as-is), **asserted** (our intuition, unmeasured), or
**known-wrong** (mathematically incorrect, fix regardless) — plus the exact statistic that would
validate it and the data needed.

**Principle (project standing rule): nothing asserted here stays a hard-coded constant once real
data exists.** Each becomes a measured parameter or is replaced. This ledger exists because the GTM
is a verifiable track record — asserted-but-unmeasured parameters undermine the entire pitch.

The published `H₀` result is validated against *simulated* seasons sampled from actual performances.
This project has the real 2025-26 backfill, so several of these are checkable here in ways the
papers did not check them.

Status legend: **INHERITED** · **ASSERTED** · **KNOWN-WRONG** · **HEURISTIC**

---

## A-DRAFT-1. Uniform per-player variance — KNOWN-WRONG (paper's own #1 limitation)

**Claim:** every player's counting stats share a standard deviation `mτ`, and percentage stats share
`rτ`. Used to make the G-score denominator tractable.

**Why wrong:** production consistency varies enormously between players. A high-usage guard's
week-to-week assist spread is nothing like a bench big's. Treating them as equal mis-prices exactly
the consistency the H2H format rewards.

**Fix (D5):** use measured per-player per-game σ from `store.player_distribution`, already validated
on the real backfill for `matchup-projection`. Retain uniform-τ as a labeled ablation baseline.

**Validate:** replay with measured-σ vs uniform-σ and compare strategy win rates. If measured σ does
*not* improve on uniform, that is a publishable finding in itself and the simpler form should win.
**Data:** real per-player game logs (have).

**MEASURED 2026-08-17 (real 2025-26, pool 156).** Switching from uniform τ to measured per-player τ
**reorders 45 of the top 50**, max rank shift 24. So the paper's simplification is not a technicality
at draft-relevant ranks — it changes who you take. Which form *wins* still requires the replay
(task 3.7); this only establishes that the choice is material. `VarianceMode.UNIFORM` is retained
as the ablation arm.

---

## A-DRAFT-2. Player distributions are known exactly and static across the season — INHERITED

**Claim:** a player's true production distribution is fixed and known at draft time.

**Reality:** roles change (trades, injuries, breakouts). This is *the* reason the in-season Co-GM
exists. At draft time it is a more defensible simplification than mid-season, but it systematically
understates the value of players with wide role uncertainty (rookies, post-trade situations) and
overstates the reliability of any point projection.

**Fix:** projections carry an uncertainty band on the mean, not just on game-to-game variance — this
repo already made exactly this correction for `matchup-projection` (commit `f628629`, "account for
estimated-mean uncertainty"). Apply the same treatment here.

**Validate:** measure realized-vs-projected dispersion by preseason role certainty bucket.
**Data:** needs a completed season projected in advance; 2025-26 backtest is the proxy.

---

## A-DRAFT-3. Categories are independent — INHERITED, measurably false

**Claim:** category outcomes are independent, which lets the most-categories objective factorize
into 2^(C−1) enumerable scenarios.

**Reality:** already measured false in this repo (commit `5ccbc0c`, category correlations reframing
wire availability as a bundle problem). Points/FG%/FGA move together; steals and blocks do not.

**Impact:** correlation changes `P(win ≥5 of 9)` even when every marginal `P(win c)` is unchanged.
Positively correlated categories make extreme outcomes (8-1, 1-8) more likely and coin-flip
matchups less likely — which is precisely the regime punting exploits.

**v1 decision:** ship the independent form (it is what the published result validates), then measure.
**Validate:** compare independent-factorized `P(win ≥5)` against a Monte-Carlo draw from the measured
category covariance, using the existing `bootstrap_category_winprob` machinery from A3 of the
projection work. **Data:** real box scores (have) + existing correlation measurement (have).

---

## A-DRAFT-4. `κ` — the period-to-period variance weight — HEURISTIC

**Claim:** a single constant relates player-to-player and period-to-period variance in the G-score
denominator.

**Reality:** `κ` should depend on games per scoring period, roster size, and category. A 4-game week
and a 2-game week are not the same problem, and the whole point of the metric is that period
structure matters.

**Validate:** derive `κ` per category from the real backfill (weekly aggregation of game logs), and
compare against the paper's value. Check sensitivity: if strategy win rate is flat in `κ` over a
plausible range, stop tuning and say so. **Data:** real game logs + a weekly period calendar (have).

**MEASURED 2026-08-17 (real 2025-26, pool 156). Resolution: stop tuning κ.** Board movement vs κ=0,
top 50:

| κ | rank changes | max shift |
|---|---|---|
| 0.0 | 0 | 0 |
| 0.5 | 44 | 18 |
| 1.0 | 47 | 20 |
| 2.0 | 47 | 23 |
| 4.0 | 46 | 25 |
| 8.0 | 46 | 25 |

The decision that matters is **whether period variance is counted at all** (κ=0 → κ=0.5 moves 44 of
50); past κ≈1 the board is essentially saturated. So κ is not a parameter worth fitting — κ=1.0 is
kept and labeled, and the sensitivity table is the justification rather than a tuned value.

**Related measurement — period noise is first-order, not a correction.** τ̄/σ by category:

| | pts | reb | ast | stl | blk | fg3m | fg_pct | ft_pct | tov |
|---|---|---|---|---|---|---|---|---|---|
| τ̄/σ | 1.39 | 1.08 | 0.90 | **1.79** | 0.98 | 1.20 | **1.72** | 1.44 | 1.39 |

Week-to-week noise equals or exceeds player-to-player spread in 7 of 9 categories — the term z-score
drops is comparable in size to the term it keeps. Note this partially refutes the original intuition
(A1) that stl/blk/ast are jointly the high-variance group: **stl is the noisiest by this measure, but
ast (0.90) and blk (0.98) are among the *quietest*.** This is a different statistic from A1's
coefficient of variation — it is period noise *relative to how much players differ* — so it is a
complement to that finding, not a contradiction of it. The two highest ratios (stl, fg_pct) are also
two of the three categories season replay found weakest, which is unlikely to be coincidence.

---

## A-DRAFT-5. Own-built projections are good enough — ASSERTED, highest-risk item

**Claim:** a minutes/role projection built from this store is accurate enough that the optimizer's
edge survives.

**Why it matters:** a superior optimizer fed bad means loses to a z-score tool fed good means. This
is the assumption most likely to sink the product, and it is entirely unmeasured today.

**Validate:** backtest 2026-27 projections' *method* on 2025-26 — project that season from
2024-25-and-prior inputs and score MAE on minutes and on each category, against (a) last-season
naive, (b) a published projection set as a reference point. **Gate: if own-built cannot beat naive
last-season carry-forward on minutes MAE, it is not ready and the honest move is to say so.**
**Data:** requires 2024-25 backfill in addition to 2025-26 — *not currently held*.

---

## A-DRAFT-6. Rookie draft-position prior — ASSERTED

**Claim:** expected rookie minutes/production can be predicted from draft slot and landing spot.

**Reality:** rookie outcomes are famously high-variance and the sample per slot is small. This is a
prior, not a model, and its variance should be wide enough to reflect that.

**Validate:** fit on historical rookie seasons, report out-of-sample error by slot bucket, and make
the resulting uncertainty band explicit in the projection. If the band is as wide as the signal, say
so and let the optimizer price the uncertainty rather than hiding it.
**Data:** multi-season rookie histories — *not currently held*.

---

## A-DRAFT-7. Expected games played is separable from per-game production — ASSERTED

**Claim:** value factorizes as `E[games] × E[per-game]`.

**Reality:** these correlate. Players returning from injury are often minutes-limited; load
management concentrates on high-usage veterans. The factorization overstates the value of a
high-rate, low-availability player.

**Validate:** measure the correlation between games played and per-game production within player-season,
and between availability and minutes on return. **Data:** real game logs + injury designations (have).

---

## A-DRAFT-8. ADP-driven bots represent real drafters — ASSERTED

**Claim:** modeling opponents as ADP + noise is a faithful enough draft-room simulation.

**Reality:** real drafters exhibit positional runs, homer picks, and correlated punt strategies that
independent ADP sampling will not produce. The paper's simulations assume even less (near-random),
so this is an improvement on the published baseline, but it is not reality.

**Consequence if wrong:** scarcity is mis-estimated, so the engine's willingness to wait on a
position is mis-calibrated — the error shows up as reaching or as being sniped.

**Validate:** compare bot-generated draft boards against real completed 2025-26 draft boards
(available via the Yahoo import work) — distribution of pick-vs-ADP deviation, run lengths by position.
**Data:** real drafts from imported leagues — *dependency on the parallel Yahoo branch*.

---

## A-DRAFT-9. Gradient descent reaches a good-enough optimum — HEURISTIC

**Claim:** warm-started Adam on a non-convex objective lands close enough to optimal.

**Validate:** on a subset of picks, brute-force or multi-start heavily and measure the objective gap
against the warm-started single run. If the gap is material on early picks (where the strategy space
is widest), raise multi-start count there only. **Data:** none external; a compute experiment.

**REPLAY VERDICT (2026-08-17): more optimizer steps are not the answer.** Quadrupling the Adam
budget (5 → 20 steps) changed H₀'s replay result by 0.3pp — inside noise. Whatever is wrong is
structural, not under-convergence. Multi-start remains untried and is a *different* remedy from more
steps; see `results.md` for the full candidate list, of which the single-representative-opponent
simplification is the leading suspect.

**OPEN — one suspicious result to adjudicate (2026-08-17).** On a real 2025-26 smoke test, after
taking Jokić first overall the optimizer settled on a strategy with a **negative rebound weight
(−1.25) and P(win reb) = 0.09** — i.e. it punts rebounds while holding the best rebounder in the
pool. The resulting 5-category build (pts .83 / ast .88 / fg3m .87 / ft_pct .89 / stl .69) is
internally coherent for a majority objective, and a negative weight legitimately means "do not spend
*future* picks on rebounding". But conceding a category your best asset dominates is the signature of
a **local optimum**, not obviously of good play.

Do not resolve this by argument — it is exactly what task 3.7's replay is for. Concretely: compare
warm-started single-run H₀ against heavy multi-start on pick 2, and check whether the punt-reb line
survives. If it does not, raise multi-start on early picks (where the strategy space is widest and
the warm start is least informative).

---

## A-DRAFT-10. Category ties are ignored — KNOWN-WRONG (small)

**Claim:** in the normal approximation a category differential of exactly zero has probability zero,
so `P(win) + P(lose) = 1`.

**Reality:** categories are integer-valued (and H2H rules usually score a tie as half a win), so exact
ties happen — most often in low-volume categories like blocks and steals, where a week's differential
is a small integer.

**Current state:** `category_win_prob` accepts a `tie_margin` continuity correction that treats
`|diff| < margin` as a tie worth half a win. It defaults to 0 (ties ignored), because the right
margin per category is unmeasured.

**Validate:** measure the empirical frequency of exact category ties per category from real weekly
matchup results, then set `tie_margin` per category from that rather than globally.
**Data:** weekly category tallies from simulated/imported leagues (have).
