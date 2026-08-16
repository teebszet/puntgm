Phases are ordered by the **critical path**, not by capability. Phase 1 and Phase 2 are independent
of each other by construction (design D11) and should proceed in parallel. Phase 1 carries the
differentiator; Phase 2 is the long pole and gates both calendar dates.

**Cut line if the calendar bites:** reduce Phase 2 sophistication (ship a crude, clearly-labeled
minutes model), never Phase 1. A weak projection behind a strong optimizer is a product; a strong
projection behind a z-score ranker is a commodity.

## 1. X-score basis and the static reduction (unblocks everything; also the fallback)

- [ ] 1.1 Implement the variance-aware standardisation basis alongside the existing z-score in `valuation.py`; counting cats use player-to-player plus period-to-period spread, percentage cats use the volume-weighted impact form
- [ ] 1.2 Derive per-category `κ` from the real 2025-26 backfill by weekly aggregation (A-DRAFT-4); report sensitivity and stop tuning if win rate is flat in it
- [ ] 1.3 Wire measured per-player σ from `store.player_distribution` into the basis; keep a uniform-σ mode as a labeled ablation (D5, A-DRAFT-1)
- [ ] 1.4 Static G-score reduction produces a complete ranked draft board from the basis
- [ ] 1.5 Retain the existing z-score valuation unchanged as a labeled replay baseline
- [ ] 1.6 Test: equal means with unequal measured variance rank differently; low-volume high-percentage players do not top percentage cats; z-score baseline is unperturbed

## 0. Shared base (landed before the Track A / Track B fork)

- [x] 0.1 `FANTASY_GM_DATA_DIR` override in `Config.data_dir` so parallel worktrees share one backfilled store instead of each re-running the backfill
- [x] 0.2 `ActualsProjectionSource` — the replay oracle that makes D11 real: it hands the engine a completed season's realized production, so Track A needs nothing from Track B. Guarded (`replay_only`, raises `LookaheadError` if asked for a season still in progress) so it can never leak into a live path

## 2. Projections (long pole — starts now, in parallel with 1 and 3)

- [x] 2.1 Define the `ProjectionSource` interface: per-category mean, production variance, mean-uncertainty band, expected games played. `as_of` is part of the signature so 2.11's no-lookahead requirement is structural, not a convention
- [x] 2.2 Fixture source with fixed values, for deterministic engine tests
- [x] 2.3 Store extensions: forward-season team, depth-chart position, offseason transactions, incoming players without NBA history, ADP — all effective-dated; plus `draft_pool_asof` so rookies without logs are draftable
- [x] 2.4 ADP ingestion from Yahoo `draft_analysis` (free with the OAuth in 4.1); represent missing ADP explicitly — parser + ingest + explicit-absence view (`projections/adp.py`, `fantasy-gm adp`). Identity resolves by folded name and reports every miss, since the Yahoo id crosswalk is still on the parallel branch. **The live fetch raises rather than stubs**: it needs the OAuth flow from 4.1; a saved payload works today
- [x] 2.5 Minutes/role model: project minutes from depth-chart position and role, reacting to team changes (D8) — **this is the bulk of the work in this change**. History and stated role are combined by inverse variance, so a thin history leans on the role curve and a settled one does not; a team change widens the band (measured ×1.45) without shifting the mean. Every parameter fit, none asserted (A-DRAFT-11); `depth_chart_pos` is read as rotation rank, which the store forces (A-DRAFT-10)
- [x] 2.6 Per-category rate projection conditioned on projected minutes — shrunk per-minute rates × projected minutes, with the rate prior taken from the player's rotation tier and the variance-vs-minutes exponent regressed rather than assumed
- [x] 2.7 Expected games played as a separate output with its own uncertainty (D7, A-DRAFT-7); measure the games-played/production correlation rather than assuming separability — **measured, and separability is false**: corr(games, min/g) = +0.479, minutes on return from an absence = 0.907×. Reported, not modeled; see A-DRAFT-7 for why
- [x] 2.8 Mean-uncertainty band distinct from production variance, following the `matchup-projection` treatment in `f628629` (A-DRAFT-2) — delta method through `rate × minutes`, so both estimation errors reach the engine
- [x] 2.9 Rookie prior fit from historical rookie seasons by draft slot, plus a manual override table; both labeled prior-derived in output (D9, A-DRAFT-6) — the asserted surface is one rank per slot bucket, refit from a past cohort the moment one exists; output carries `prior_basis` = fitted/fallback
- [ ] 2.10 **Backfill 2024-25** — required for the backtest in 2.11, not currently held. Store has 2025-26 only (26,651 logs, 506 players with ≥10 games). Must be run from a network that can reach `stats.nba.com`:
      `FANTASY_GM_DATA_DIR=/Users/tim/projects/fantasy-nba-gm/data python -m fantasy_gm.cli backfill --season 2024-25`
      **Still blocked.** `stats.nba.com` is unreachable from the environment Track B was built in (connection times out, both through `nba_api` and directly), so this has to be run from the user's own network.
- [ ] 2.11 Backtest the method on 2025-26 from pre-season inputs; report minutes and per-category MAE against naive carry-forward. **Gate: if it cannot beat naive carry-forward on minutes MAE, say so plainly and do not ship it as a model** (A-DRAFT-5)
      **Harness landed, gate not passed.** `projections/backtest.py` + `fantasy-gm projection-backtest` run in both modes; cross-season reports the 2.10 blocker rather than degrading into a number that looks like a result. The split-season proxy that runs today is **INCONCLUSIVE**: minutes MAE 2.97 vs naive 3.04 (+2.3%) is only 0.7σ paired, closer on 53% of players. 7 of 9 categories beaten. Not evidence of an edge — see A-DRAFT-5
- [x] 2.12 Test: forward-only reads (no lookahead); a team change moves projected minutes; rookie output is labeled; backtest uses no in-season information

## 3. H₀ optimizer and draft replay (the differentiator; needs no projections — D11)

- [ ] 3.1 Category differential model over the five player groups: own drafted, candidate, own future, opponent known, opponent future — mean and variance including future-pick player-to-player spread
- [ ] 3.2 Objective functions: each-category (`Σ P(win c)`) and most-categories (`P(win ≥ ⌈C/2⌉)` with tie handling, 256-scenario enumeration for 9 cats)
- [ ] 3.3 Positional assignment via Jonker-Volgenant; drafted-eligible 0 / ineligible −∞ / future picks by category alignment plus flex bonus; report infeasible roster structures (D4)
- [ ] 3.4 Gradient descent (Adam) over per-category weights and flex shares, warm-started from the previous round
- [ ] 3.5 ADP-driven opponent bots with noise; survival probability to the deciding team's next pick (D10)
- [ ] 3.6 League-settings parameterization: categories, roster slots, teams, rounds, objective (D12)
- [ ] 3.7 **Draft replay harness** using 2025-26 realized production as the projection input — head-to-head H₀ vs G-score vs z-score vs ADP, reported across draft slots
- [ ] 3.8 Validate against the published simulation results as a correctness check before trusting the real-data numbers
- [ ] 3.9 Local-optimum audit: multi-start on a subset of picks, measure the objective gap, raise multi-start only where it matters (A-DRAFT-9)
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
