Phases are ordered by the **critical path**, not by capability. Phase 1 and Phase 2 are independent
of each other by construction (design D11) and should proceed in parallel. Phase 1 carries the
differentiator; Phase 2 is the long pole and gates both calendar dates.

**Cut line if the calendar bites:** reduce Phase 2 sophistication (ship a crude, clearly-labeled
minutes model), never Phase 1. A weak projection behind a strong optimizer is a product; a strong
projection behind a z-score ranker is a commodity.

## 1. X-score basis and the static reduction (unblocks everything; also the fallback)

- [x] 1.1 Implement the variance-aware standardisation basis alongside the existing z-score, in `fantasy_gm/draft/xscore.py`; counting cats use player-to-player plus period-to-period spread, percentage cats use the volume-weighted impact form. Periods are real ISO weeks aggregated from game logs, and **idle weeks inside a player's active span count as zero** — that is where most realized category variance lives
- [x] 1.2 Derive per-category `κ` from the real 2025-26 backfill by weekly aggregation (A-DRAFT-4); report sensitivity and stop tuning if win rate is flat in it. **Resolved: κ saturates past ≈1.0** — the decision is whether to count period variance at all, not what κ equals. `kappa_sensitivity()` is the reported evidence
- [x] 1.3 Wire measured per-player τ into the basis; keep a uniform-τ mode as a labeled ablation (D5, A-DRAFT-1). **Measured: reorders 45 of the top 50** — the ablation is worth running in replay
- [x] 1.4 Static G-score reduction produces a complete ranked draft board from the basis (`g_score_board`, with per-category breakdown for explainability and for H₀ to consume)
- [x] 1.5 Retain the existing z-score valuation unchanged as a labeled replay baseline — `fantasy_gm/valuation.py` untouched
- [x] 1.6 Test: equal means with unequal measured variance rank differently; κ flips a higher-mean/higher-variance player below a steady one; low-volume high-percentage players do not top percentage cats; idle weeks raise τ; z-score baseline is unperturbed (14 tests)

## 0. Shared base (landed before the Track A / Track B fork)

- [x] 0.1 `FANTASY_GM_DATA_DIR` override in `Config.data_dir` so parallel worktrees share one backfilled store instead of each re-running the backfill
- [x] 0.2 `ActualsProjectionSource` — the replay oracle that makes D11 real: it hands the engine a completed season's realized production, so Track A needs nothing from Track B. Guarded (`replay_only`, raises `LookaheadError` if asked for a season still in progress) so it can never leak into a live path

## 2. Projections (long pole — starts now, in parallel with 1 and 3)

- [x] 2.1 Define the `ProjectionSource` interface: per-category mean, production variance, mean-uncertainty band, expected games played. `as_of` is part of the signature so 2.11's no-lookahead requirement is structural, not a convention
- [x] 2.2 Fixture source with fixed values, for deterministic engine tests
- [x] 2.3 Store extensions: forward-season team, depth-chart position, offseason transactions, incoming players without NBA history, ADP — all effective-dated; plus `draft_pool_asof` so rookies without logs are draftable
- [ ] 2.4 ADP ingestion from Yahoo `draft_analysis` (free with the OAuth in 4.1); represent missing ADP explicitly
- [ ] 2.5 Minutes/role model: project minutes from depth-chart position and role, reacting to team changes (D8) — **this is the bulk of the work in this change**
- [ ] 2.6 Per-category rate projection conditioned on projected minutes
- [ ] 2.7 Expected games played as a separate output with its own uncertainty (D7, A-DRAFT-7); measure the games-played/production correlation rather than assuming separability
- [ ] 2.8 Mean-uncertainty band distinct from production variance, following the `matchup-projection` treatment in `f628629` (A-DRAFT-2)
- [ ] 2.9 Rookie prior fit from historical rookie seasons by draft slot, plus a manual override table; both labeled prior-derived in output (D9, A-DRAFT-6)
- [ ] 2.10 **Backfill 2024-25** — required for the backtest in 2.11, not currently held. Store has 2025-26 only (26,651 logs, 506 players with ≥10 games). Must be run from a network that can reach `stats.nba.com`:
      `FANTASY_GM_DATA_DIR=/Users/tim/projects/fantasy-nba-gm/data python -m fantasy_gm.cli backfill --season 2024-25`
- [ ] 2.11 Backtest the method on 2025-26 from pre-season inputs; report minutes and per-category MAE against naive carry-forward. **Gate: if it cannot beat naive carry-forward on minutes MAE, say so plainly and do not ship it as a model** (A-DRAFT-5)
- [ ] 2.12 Test: forward-only reads (no lookahead); a team change moves projected minutes; rookie output is labeled; backtest uses no in-season information

## 3. H₀ optimizer and draft replay (the differentiator; needs no projections — D11)

- [x] 3.1 Category differential model over the five player groups: own drafted, candidate, own future, opponent known, opponent future — mean and variance including future-pick player-to-player spread. **Opponents are modelled as drafting best-available under neutral weights, not as the pool average** — the latter hands us a free edge per remaining round and inflates early-draft confidence
- [x] 3.2 Objective functions: each-category (`Σ P(win c)`) and most-categories. Majority probability uses a Poisson-binomial DP in O(C²) rather than enumerating 2^(C−1) scenarios — same exact quantity, but it sits in the innermost loop. Optional `tie_margin` continuity correction for integer categories
- [x] 3.3 Positional assignment via Jonker-Volgenant (implemented directly; scipy is not a dependency and a 13×13 solve does not justify one). Pinned against brute-force permutation search. Infeasible rosters surface as unplaced players rather than an exception
- [x] 3.4 Gradient descent (Adam, numeric gradient) over per-category weights, warm-started from the previous round
- [x] 3.5 ADP-driven opponent bots with noise; survival probability to the deciding team's next pick (D10). Reuses `simulate._adp_order` so replay drafts and simulated leagues share one notion of "what the market thinks". **Yahoo ADP is gated, so this is a value-ranking proxy — a proxy market is not wrong in the way a real market is wrong, so replay edges built on it read as a lower bound, not an estimate**
- [x] 3.6 League-settings parameterization: categories, roster slots, teams, rounds, objective (D12)
- [x] 3.7 **Draft replay harness** using 2025-26 realized production as the projection input — head-to-head H₀ vs G-score vs z-score vs ADP, reported across draft slots. Grading is **all-play-all** (every team vs every other, every week) rather than a round-robin schedule, so results cannot depend on the schedule generator or on who a team happened to draw. Seat assignment rotates so draft position is controlled. **Harness built and running; see `results.md` for what it found — the first answer is not the one the papers predict**
- [ ] 3.8 Validate against the published simulation results as a correctness check before trusting the real-data numbers. **Now the priority**: replay says H₀ loses to the static G-score board, so the question is whether this implementation reproduces the paper at all
- [ ] 3.9 Local-optimum audit: multi-start on a subset of picks, measure the objective gap, raise multi-start only where it matters (A-DRAFT-9). **More Adam steps are ruled out** (5→20 moved the result 0.3pp); multi-start is a different remedy and is untried
- [ ] 3.12 **Test the representative-opponent simplification** — leading suspect for H₀'s underperformance. H₀ optimizes against one opponent but is graded against eleven, so a conceded category is far more expensive than its objective believes
- [ ] 3.13 **Schedule-based grading as a robustness check** — all-play-all removes schedule luck but may systematically penalise the concentrated builds H₀ discovers. Cuts both ways; needs measuring, not arguing
- [ ] 3.10 Measure the category-independence error: independent-factorized `P(win ≥5)` vs Monte-Carlo from the measured covariance, reusing `bootstrap_category_winprob` (A-DRAFT-3)
- [ ] 3.11 Test: same player valued differently by different rosters; concentration emerges without declaration and is reversible; future-pick variance shrinks as the draft progresses; multi-eligible players priced above equivalent single-position players

## 4. Draft surface

- [ ] 4.1 Yahoo OAuth + `draft_results` polling for live pick ingestion — **consumes the parallel branch's fetch layer; do not rebuild it** (D11, proposal Impact)
- [ ] 4.2 Manual pick entry sufficient to drive a full draft unaided; type-ahead player resolution through the existing name crosswalk
- [ ] 4.3 Ingestion failure degrades to manual against state ingested so far; discrepancies between ingested and platform state are surfaced, never silently reconciled
- [ ] 4.4 On-the-clock output: ranked candidates with pick value, category impact, survival probability
- [ ] 4.5 Time-bounded evaluation — degrade to the static reduction rather than exceed the clock, and say so when it happens
- [ ] 4.6 Completed-draft grading: per-team category profile, standing, build identity, costliest picks vs the alternative available; works on boards the engine did not draft
- [ ] 4.7 CLI: `draft` (live), `grade` (completed board), `draft-replay` (validation)
- [ ] 4.8 **Dry-run the full live path against a real Yahoo mock draft before the real draft** — the fetch layer has never run against a live token and draft day is the wrong time to find out

## 5. Log and glue

- [ ] 5.1 Draft-pick record type: draft state, ranked candidates with values, recommendation, actual pick; append-only integrity preserved
- [ ] 5.2 Reproducible-from-log: recorded state + projection source + settings re-run to identical values
- [ ] 5.3 Post-season grading of logged picks against alternatives available at that pick, reported per category
- [ ] 5.4 Test: append-only holds; a divergent pick records both recommendation and actual; replay of a record reproduces its values

## 6. Close-out

- [ ] 6.1 Resolve every ledger item in `assumptions.md` to measured, replaced, or explicitly-deferred-with-reason
- [ ] 6.2 `openspec validate h-score-draft-engine --strict` + `pytest -q` + `ruff check fantasy_gm tests`
- [ ] 6.3 Write up the replay result for publication — including the honest baseline framing (compare against the strongest baseline, not the weakest; the waiver-replay lesson about `most_games` being a strawman applies directly)
