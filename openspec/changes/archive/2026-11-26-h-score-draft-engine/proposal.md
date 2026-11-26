## Why

The draft is the single highest-leverage decision set in a category league, and it is the one
moment the whole addressable audience is actively shopping for tools. The acquisition window is
**mid-September to late October 2026**; miss it and the next one is a year out.

There is also an unusually clean opening. A published line of work establishes that the metric
every commercial tool uses is **the wrong metric for weekly H2H**, and nobody has shipped the
correction:

- **[arXiv 2307.02188](https://arxiv.org/abs/2307.02188)** — Z-score is a special case of a more
  general metric under the assumption that *future player performance is known exactly*. In weekly
  H2H that assumption is badly wrong: you win a category by beating one opponent over ~4 games, so
  week-to-week variance is first-order. The correction, **G-score**, adds a period-to-period
  variance term to the denominator. A Z-score drafter in a G-score field wins **0.4–1.1%** of
  seasons against an 8.3% baseline; a G-score drafter in a Z-score field wins **32.5%**.
- **[arXiv 2409.09884](https://arxiv.org/abs/2409.09884)** — a *static ranking list* is itself the
  wrong shape. **H-scoring** (implementation `H₀`) re-optimizes at every pick against the roster
  already assembled, the opponents' known rosters, and the expected distribution of future picks.
  It beats G-score (21.8% each-category / 37.7% most-categories vs 8.3% baseline) and **learns to
  punt implicitly** rather than exposing punting as a checkbox.

Basketball Monster, Hashtag Basketball, and FantasyPros Draft Wizard are all z-score-and-adjustments.
BBM's `DynV` recalculates against the *remaining pool* — pool-conditional, not roster-conditional,
and still variance-blind. Punting is a manual checkbox everywhere.

So the claim is not "we invented something." It is **"we are the only shipped implementation of the
method the literature already validated, and we can prove it with a replay harness nobody else has."**
That is a far easier claim to defend than novelty, and it is exactly the project's standing GTM
thesis — *the product's track record is the content* — applied to drafting.

Critically, `H₀`'s own stated future work reads as a description of this repo: per-player variance
forecasting, week-to-week category correlations, opponent-strategy adaptation, and **waiver-wire
integration**. The draft engine and the in-season Co-GM are not two products; the draft is pick 1 of
a season-long optimization, and this change is where that becomes literal.

## What Changes

- **Add `draft-engine`** — pick valuation as **category win probability**, not a scalar. One
  `X-score` basis (a category-standardised, variance-aware unit) underpins two modes: a *static*
  G-score reduction and the *dynamic* `H₀` optimizer that re-solves at each pick over the deciding
  team's roster, known opponent rosters, expected future picks, and positional assignment. Includes
  **draft replay validation** — H₀ vs G-score vs Z-score vs ADP over real 2025-26 data.
- **Add `player-projections`** — forward-season projection behind a pluggable `ProjectionSource`
  interface, with an own-built implementation: a **minutes/role model** (the dominant term in
  fantasy value), an explicit **rookie path** (no NBA history exists to project from), and
  **expected games played** as a first-class output.
- **Add `draft-surface`** — pick ingestion during a live draft (Yahoo `draft_results` polling plus
  a manual fallback that degrades gracefully), and post-hoc **draft grading** for a completed board.
- **Modify `historical-data-pipeline`** — 2026-27 forward-looking inputs the store does not hold:
  rosters, depth charts, offseason transactions, and ADP (sourced from Yahoo `draft_analysis`, free
  with the OAuth the live sync already requires).
- **Modify `recommendation-log`** — a **draft-pick record type**, so every pick the engine
  recommends is logged with its as-of state and is scoreable by the same replay machinery that
  grades waiver calls.

## Capabilities

### New Capabilities

- `draft-engine`: Variance-aware, roster-conditional pick valuation optimizing category win
  probability, with positional assignment, an ADP-driven opponent model, and draft replay validation.
- `player-projections`: Forward-season per-player category projections with measured variance and
  expected games played, behind a pluggable source interface.
- `draft-surface`: Live draft pick ingestion and completed-draft grading.

### Modified Capabilities

- `historical-data-pipeline`: Adds forward-season roster/depth-chart/transaction inputs and ADP.
- `recommendation-log`: Adds a scoreable draft-pick record type.

## Impact

- New `fantasy_gm/draft/` (X-score basis, G-score, H₀ optimizer, positional assignment, opponent
  model, replay) and `fantasy_gm/projections/` (source interface, minutes/role, rookies).
- `valuation.py` gains the X-score basis alongside the existing z-score, which is retained as a
  labeled replay baseline — the thing H₀ must beat, per project convention.
- Store extensions for forward rosters, depth charts, transactions, and ADP.
- CLI first (`draft`, `grade`, `draft-replay`); a web surface for grading is a follow-up change.
- **Depends on** the Yahoo fetch layer and strategy-baseline replay work in flight on a parallel
  branch. This change consumes those; it does not rebuild them. See `design.md` D11.

## Non-goals

- **Roto and auction formats.** The objective function is pluggable and roto is a known-different
  optimization ([arXiv 2501.00933](https://arxiv.org/pdf/2501.00933)), but v1 is 9-cat H2H snake only.
- **Keeper/dynasty.** Reduces the pool and adds a multi-year horizon; out of scope.
- **Points leagues.** Off-thesis — the engine is category-relative by construction.
- **A web UI.** This change ships the engine and a CLI. The public grading surface is a follow-up
  that depends on this one landing.
- **LLM calls.** The optimizer is deterministic and testable, so replay has a clean baseline.
