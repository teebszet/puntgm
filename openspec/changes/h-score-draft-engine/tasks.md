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
- [x] 2.3a **Fill those tables** — 2.3 landed the schema and the reads; nothing wrote to them, so the role mechanism was inert on real data. `data/player_index.py` + `fantasy-gm player-index` ingest NBA `playerindex` (one batched call) into `player_positions` (new — the input D4's slot assignment needs, since box scores carry no position at all), `forward_roster`, and `incoming_players`. Depth is **derived** by ranking each new roster on its players' own minutes history rather than sourced from an external depth chart (A-DRAFT-12); hand-entered rows with a later `known_from` supersede it. **Ingested for 2026-27** (`--known-from 2026-08-17`, from the cached payload): 580 rows / 30 teams → 575 positions, 580 forward-roster rows, 81 incoming players; 96 movers re-ranked onto new teams, 99 no-history players ranked last. The first ingest wrote nothing but positions — `ROSTER_STATUS` arrives as a JSON float (`1.0`) and the strict `int(str(v))` parse read every player as unrostered, so `is_rostered` was false 580 times over. Fixed in `_as_int`; the test fixtures had hand-typed the field as an int and so never saw it
- [x] 2.4 ADP ingestion from Yahoo `draft_analysis` (free with the OAuth in 4.1); represent missing ADP explicitly — parser + ingest + explicit-absence view (`projections/adp.py`, `fantasy-gm adp`). Identity resolves by folded name and reports every miss, since the Yahoo id crosswalk is still on the parallel branch. **The live fetch raises rather than stubs**: it needs the OAuth flow from 4.1; a saved payload works today
- [x] 2.5 Minutes/role model: project minutes from depth-chart position and role, reacting to team changes (D8) — **this is the bulk of the work in this change**. History and stated role are combined by inverse variance, so a thin history leans on the role curve and a settled one does not; a team change widens the band (measured ×1.45) without shifting the mean. Every parameter fit, none asserted (A-DRAFT-11); `depth_chart_pos` is read as rotation rank, which the store forces (A-DRAFT-10)
- [x] 2.6 Per-category rate projection conditioned on projected minutes — shrunk per-minute rates × projected minutes, with the rate prior taken from the player's rotation tier and the variance-vs-minutes exponent regressed rather than assumed
- [x] 2.7 Expected games played as a separate output with its own uncertainty (D7, A-DRAFT-7); measure the games-played/production correlation rather than assuming separability — **measured, and separability is false**: corr(games, min/g) = +0.479, minutes on return from an absence = 0.907×. Reported, not modeled; see A-DRAFT-7 for why
- [x] 2.8 Mean-uncertainty band distinct from production variance, following the `matchup-projection` treatment in `f628629` (A-DRAFT-2) — delta method through `rate × minutes`, so both estimation errors reach the engine
- [x] 2.9 Rookie prior fit from historical rookie seasons by draft slot, plus a manual override table; both labeled prior-derived in output (D9, A-DRAFT-6) — the asserted surface is one rank per slot bucket, refit from a past cohort the moment one exists; output carries `prior_basis` = fitted/fallback
- [x] 2.10 **Backfill 2024-25** — required for the backtest in 2.11. **Done 2026-08-17**, run by the user from their own network. The store holds three complete seasons: 2023-24 (26,401 logs / 572 players), 2024-25 (26,306 / 569), 2025-26 (26,651 / 582), 3,690 games, and all three raw `LeagueGameLog` payloads sit in `data/raw_cache`, so it replays offline. This unblocks 2.11's cross-season gate *and* retires the single-season caveat on the draft replay. Track B recorded this as still-blocked for a day after it had in fact been run — the `stats.nba.com` block is real and still in force (it times out from the user's own machine too, and `cdn.nba.com` 403s), but it was not what gated 2.11
- [x] 2.10a **Opening-night 2025-26 rosters, reconstructed** — `data/reconstruct.py` + `fantasy-gm reconstruct-rosters`. Each player's team in their first game of the season, within 14 days of tip-off; depth derived from pre-cut history by the existing `build_forward_roster`, so no production from the scored season reaches the projection. 442 rows / 30 teams / 92 movers → 371 of 608 players on a stated rank at mean `role_weight` 0.703.
      **Compromise, stated in the module docstring and printed on every run:** membership is read from rows dated after the cut, and anyone who missed the whole season is invisible, so the pool skews toward players who stayed healthy. Guarded — refuses a cut inside the season, marks every row `reconstructed:`, and clears its own snapshot before writing so a narrower re-run cannot leave a wider one's players behind
- [ ] 2.11 Backtest the method on 2025-26 from pre-season inputs; report minutes and per-category MAE against naive carry-forward. **Gate: if it cannot beat naive carry-forward on minutes MAE, say so plainly and do not ship it as a model** (A-DRAFT-5)
      **Cross-season gate now RUN, and INCONCLUSIVE.** Fit through 2025-10-20, scored on all of 2025-26, 379 players: minutes MAE 4.47 vs naive 4.50 (+0.7%, 0.2σ paired, closer on 52% of players). 9 of 9 categories beaten — not the gate, and not independent, since the categories are rates through the same minutes.
      That first run was a floor, not the model's score — the role mechanism was entirely inert (`forward_roster` held 2026-27 only, so `stated_rank` = 0 across the whole 2025-26 pool). **Re-run with 2.10a's reconstructed rosters: 4.35 vs 4.50, +3.2% at 1.1σ.** Roughly four times the edge, still inconclusive. Bias worsened +0.78 → +1.92, which the window sensitivity attributes to the reconstruction rather than the model: as rosters fill out (14.7 → 15.8 → 16.7 players/team) bias falls and MAE improves monotonically, but the wider windows admit midseason signings and are not legitimate readings. Model closer on only 50% of players throughout. **Gate stays open**; the clean read needs real `playerindex` opening-night rosters, which is what the `stats.nba.com` block actually costs — see A-DRAFT-5
- [x] 2.12 Test: forward-only reads (no lookahead); a team change moves projected minutes; rookie output is labeled; backtest uses no in-season information

## 3. H₀ optimizer and draft replay (the differentiator; needs no projections — D11)

- [x] 3.1 Category differential model over the five player groups: own drafted, candidate, own future, opponent known, opponent future — mean and variance including future-pick player-to-player spread. **Opponents are modelled as drafting best-available under neutral weights, not as the pool average** — the latter hands us a free edge per remaining round and inflates early-draft confidence
- [x] 3.2 Objective functions: each-category (`Σ P(win c)`) and most-categories. Majority probability uses a Poisson-binomial DP in O(C²) rather than enumerating 2^(C−1) scenarios — same exact quantity, but it sits in the innermost loop. Optional `tie_margin` continuity correction for integer categories
- [x] 3.3 Positional assignment via Jonker-Volgenant (implemented directly; scipy is not a dependency and a 13×13 solve does not justify one). Pinned against brute-force permutation search. Infeasible rosters surface as unplaced players rather than an exception
- [x] 3.4 Gradient descent (Adam, numeric gradient) over per-category weights, warm-started from the previous round
- [x] 3.5 ADP-driven opponent bots with noise; survival probability to the deciding team's next pick (D10). Reuses `simulate._adp_order` so replay drafts and simulated leagues share one notion of "what the market thinks". **Yahoo ADP is gated, so this is a value-ranking proxy — a proxy market is not wrong in the way a real market is wrong, so replay edges built on it read as a lower bound, not an estimate**
- [x] 3.6 League-settings parameterization: categories, roster slots, teams, rounds, objective (D12)
- [x] 3.7 **Draft replay harness** using 2025-26 realized production as the projection input — head-to-head H₀ vs G-score vs z-score vs ADP, reported across draft slots. Grading is **all-play-all** (every team vs every other, every week) rather than a round-robin schedule, so results cannot depend on the schedule generator or on who a team happened to draw. Seat assignment rotates so draft position is controlled. **Harness built and running; see `results.md` for what it found — the first answer is not the one the papers predict**
- [x] 3.8 Validate against the published simulation results as a correctness check before trusting the real-data numbers. **RESOLVED 2026-08-24: the implementation was under-built, and the published result reproduces once it is fixed.**
      First finding: **we had never measured the published quantity.** The paper runs one H₀ drafter against *eleven G-score drafters* over twenty-week resampled seasons with the true distributions known exactly, and reports the share of seasons the H₀ team finishes first (21.8% Each Category, 37.7% Most Categories, vs 8.3% chance). Our replay reports category win rate in a mixed room on one realized season. "H₀ loses to the static board" was a true statement about a different experiment.
      `fantasy_gm/draft/papersim.py` runs the published experiment, every arm paired against a twelfth-G-score-drafter null under common random numbers. **The null is load-bearing**: a snake over an odd number of rounds is strongly seat-dependent even when every drafter runs the same board, so reading a seat's title rate against 8.3% credits the seat to the algorithm.
      **The deficit was one term.** The engine valued *one* future pick and multiplied by how many remained, pricing a thirteenth-round pick like a third-round pick — measured, it expected every remaining round to land on roughly the 25th-best player available and over-valued its own future by about a third. `future_slices` prices the j-th future pick from the suffix of the board that will still be there; the slices are nested, so one backward accumulation serves them all and it costs what it replaced. Weight renormalisation (the paper's constraint, which we only approximated with a clip) adds a few points on top of that and *nothing* — sometimes less than nothing — without it.
      Measured and discarded first: the 40-man candidate shortlist was also bounding the future pool. Worth ~1%. Kept as the `h_full_pool` arm. See `results.md` and A-DRAFT-17
- [ ] 3.9 Local-optimum audit: multi-start on a subset of picks, measure the objective gap, raise multi-start only where it matters (A-DRAFT-9). **More Adam steps are ruled out** (5→20 moved the result 0.3pp); multi-start is a different remedy and is untried. **Demoted by 3.8** — the future-pick model, not the optimizer's ability to find its optimum, was the deficit. Worth re-measuring on the corrected engine before spending on it, since the objective it is now climbing is a different one
- [ ] 3.14 **Wire positional assignment into the objective.** `draft/assignment.py` has no callers: task 3.3 built the Jonker-Volgenant solver, pinned it against brute force, and nothing ever consumed it. Positional assignment is a named component of the published H₀ — it is how the optimizer keeps flex slots open for the picks it intends to make, and its output feeds the future-pick term directly. 3.8's reproduction matches the published number *without* it, in a room that constrains neither side; a live draft constrains both. Largest known gap between our H₀ and the paper's
- [x] 3.12 **Test the representative-opponent simplification** — leading suspect for H₀'s underperformance. H₀ optimizes against one opponent but is graded against eleven, so a conceded category is far more expensive than its objective believes. **Measured on three seasons: real but small.** `OpponentModel.FIELD` closes 28% of the matchup gap and 16% of the category gap vs the static board; better on matchup% in all three seasons, worse on category% in 2023-24; H₀ still loses. The informative half is that `STRONGEST` — a *tougher* single stand-in — is the worst arm in every season, so the defect is the single-opponent reduction itself, not the stand-in's calibre. Also found: the shipped selector picks by roster *length*, which a snake draft holds near-constant, so "the strongest opposing team" was really a fixed seat. See `results.md`
- [x] 3.13 **Schedule-based grading as a robustness check** — all-play-all removes schedule luck but may systematically penalise the concentrated builds H₀ discovers. Cuts both ways; needs measuring, not arguing. **Measured, and the concern was structurally void**: a round-robin plays a balanced subset of exactly the pairings all-play-all enumerates, so the two share an expectation and differ only in variance. Three seasons, both gradings, every strategy within ~0.5pp on category rate with unsystematic signs. All-play-all stays the default; the caveat is closed rather than standing. `round_robin_pairings` + `score_rosters(schedule=True)`
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

## 4b. Static board — the free surface (added 2026-08-23)

Not in the original scope: the interview chose to skip shipping G-score and go straight to H₀.
Three seasons of replay inverted that (`results.md`), and this is the only draft surface that
needs no OAuth, no forward projection and no working optimizer — so it is what can face the
mid-September window.

- [x] 4b.1 `fantasy_gm/draft/board.py` — ranked board over the categories left after a declared
      punt, with per-category breakdown and a rank delta against z-score computed on the *same*
      pool and the *same* reduced category set, so the delta isolates the metric
- [x] 4b.2 Named punt builds (`PUNT_BUILDS`), covering what a 9-cat drafter actually plays
- [x] 4b.3 **Separate availability from variance** — `AvailabilityMode` (`realized` / `neutral` /
      `projected`). Most of the board's measured edge over z-score was availability, and on a
      preseason board realized availability is hindsight (A-DRAFT-14). `projected` reuses the A13
      beta-binomial games model; `xscore.include_idle_weeks` still defaults True so no published
      replay number moves
- [x] 4b.4 Score the three treatments head-to-head in the draft replay across two seasons and two
      seeds. **Forward-honest edge over z-score is +5 to +9pp**; neutral vs projected is inside
      seed noise and neither is claimed to win
- [x] 4b.5 JSON + Markdown export with an `index.json` manifest, and a `basis` provenance line that
      the rendering layer cannot drop
- [x] 4b.6 CLI: `board` (`--build` / `--punt` / `--availability` / `--as-of` / `--movers` / `--out`)
- [x] 4b.7 Test: punting re-ranks and removes the category; a rookie takes the fitted pool
      availability rate rather than certainty; the availability variance term is binomial over
      games not Bernoulli over weeks; the provenance line names the projection date (19 tests)
- [x] 4b.9 **Steelman the z-score baseline** (`fantasy_gm/draft/zvariants.py`) — total value,
      replacement-level iteration, punt-aware subsets, naive vs fitted availability. Reproduces
      the shipped z-score exactly under defaults (pinned by test). **G-score beats per-game z by
      +11 to +20pp across three seasons and loses to total-value z by 0.7 to 3.8pp.** A-DRAFT-15
- [x] 4b.10 **Close the second hindsight leak** — forward boards constructed from per-game stats
      compounded over a *scheduled* game count rather than aggregated over active weeks, which
      had retained each player's realized games-per-week. corr(realized games, G-vs-z gain)
      +0.60/+0.64 -> +0.07/-0.10. A-DRAFT-14
- [x] 4b.11 **Fix the seat-adjacency bias in the replay harness** (`mirror=True`) — the same
      board in both seats scored up to +9.5pp for the lower-listed arm. Every table now carries
      a null arm. A-DRAFT-16
- [x] 4b.12 **Fit kappa** — 0, not the provisional 1.0; won every run of a 6-point sweep in both
      pairings. `BOARD_KAPPA = 0.0`; the engine's `DEFAULT_KAPPA` deliberately untouched pending
      task 3.8. A-DRAFT-4 resolved
- [ ] 4b.8 Publish it — blocked on `puntgm.com` + the X/IG handles existing. The board data and
      its Markdown are generated; only the page is missing

## 5. Log and glue

- [ ] 5.1 Draft-pick record type: draft state, ranked candidates with values, recommendation, actual pick; append-only integrity preserved
- [ ] 5.2 Reproducible-from-log: recorded state + projection source + settings re-run to identical values
- [ ] 5.3 Post-season grading of logged picks against alternatives available at that pick, reported per category
- [ ] 5.4 Test: append-only holds; a divergent pick records both recommendation and actual; replay of a record reproduces its values

## 6. Close-out

- [ ] 6.1 Resolve every ledger item in `assumptions.md` to measured, replaced, or explicitly-deferred-with-reason
- [ ] 6.2 `openspec validate h-score-draft-engine --strict` + `pytest -q` + `ruff check fantasy_gm tests`
- [ ] 6.3 Write up the replay result for publication — including the honest baseline framing (compare against the strongest baseline, not the weakest; the waiver-replay lesson about `most_games` being a strawman applies directly)
