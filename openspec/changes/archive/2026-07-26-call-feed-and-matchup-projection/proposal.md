## Why

An extended interview with a serious H2H 9-cat grinder reframed the product. The
value is **not** a heuristic that ranks the waiver wire into a single list — that throws
away the manager's judgment about team-build strategy and risk/reward. What a real player
wants is a **relevance-weighted call feed**: a live stream of signals (usage/efficiency
trends, role changes, injuries, opponent moves) surfaced across a *strength spectrum* and
adjusted for relevance to *their* roster, *this* matchup, and *this* stage of the season —
plus an end-of-day summary that reconciles the day's signals into candidate roster moves,
each framed as a way to play out the rest of the matchup with projected category impacts.

Underneath every part of that sits one computation the old ranker never did: **projecting
where each category lands by week's end**, as a distribution, for both teams. "Is this cat
safe / contested / gone?" and "is this signal relevant?" both fall out of that projection.

The ranked recommendation is removed. The point-in-time data layer, as-of reads, and
simulate-first league state from the previous change stay — they are the substrate this
runs on, and the baseline engine remains as the thing the new model must beat in replay.

## What Changes

- **Add `matchup-projection`** — project each scoring category's end-of-period outcome for
  both teams as a distribution: current tally + Σ(games remaining × expected-per-game with
  a per-player confidence band), yielding a per-category **win probability** and a
  safe / contested / gone label. Variance-aware (points/rebounds low-variance, steals/blocks/
  assists high-variance), injury-reactive, and tightening as the period resolves.
- **Add `call-feed`** — a stream of typed **signals** across a soft→strong strength
  spectrum, where display strength = confidence × impact-on-a-contested-category ×
  relevance-to-my-build/season-stage; plus **end-of-day reconciliation** that summarises
  the day's relevant signals into candidate roster moves with projected category deltas and
  distinct "lines of play." **Opponent modeling** (reading their adds/drops as revealed
  category strategy, and pre-emption) feeds relevance.
- **Modify `historical-data-pipeline`** — add point-in-time **usage/role** data (minutes,
  shot attempts, starter/bench, depth-chart position), **per-player production
  distributions** (mean + consistency), and **per-category variance profiles**.
- **Modify `recommendation-log`** — from ranked-candidate rows to a **scoreable call-feed
  log** of two record types (signal, reconciliation-move). Graded in replay by the
  *realized* category impact of suggested moves vs. standing pat (a clean counterfactual —
  the replay world is fully observed), plus projection calibration.
- **Remove `decision-engine`** — the ranked-list engine is superseded by
  `matchup-projection` + `call-feed`.

## Capabilities

### New Capabilities

- `matchup-projection`: Project each category's end-of-period outcome as a distribution for
  both teams, with per-category win probability and a safe/contested/gone read.
- `call-feed`: A relevance-weighted, strength-graded stream of live signals plus end-of-day
  reconciliation into candidate roster moves with projected category impacts.

### Modified Capabilities

- `historical-data-pipeline`: Adds usage/role, production distributions, and per-category
  variance profiles as point-in-time data.
- `recommendation-log`: Becomes the scoreable call-feed log (signals + reconciliation moves).

### Removed Capabilities

- `decision-engine`: The ranked streaming/waiver recommender is superseded by `matchup-projection`
  + `call-feed`. (An entire capability can't be emptied via requirement deltas, so its baseline
  spec `openspec/specs/decision-engine/` is deleted directly on archive rather than via a REMOVED
  delta.)

## Impact

- Reworks `fantasy_gm/engine/` from a ranker into a projector + feed generator; the
  baseline `DecisionEngine` is retained as a replay benchmark.
- New modules for projection, signals, relevance/opponent modeling, and reconciliation.
- Extends the store with usage/role and variance data; the recommendation log gains signal
  and reconciliation record types.
- Still no UI and no live OAuth sync in this change — it produces the engine + log the feed
  and the replay harness need. The feed is exercised offline over simulated leagues.
