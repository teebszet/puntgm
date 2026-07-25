## 1. Data extensions (point-in-time)

- [x] 1.1 Store effective-dated usage/role: minutes, shot attempts, starter/bench, depth-chart position; expose via the as-of layer
- [x] 1.2 Expose per-player expected-per-game per category with a variance/consistency measure, as of a date
- [x] 1.3 Provide per-category variance profiles (pts/reb lower-variance; stl/blk/ast higher-variance)
- [x] 1.4 Extend the synthetic season to emit usage/role trends and variance so the above are exercisable offline

## 2. Matchup projection (the spine)

- [x] 2.1 Implement per-category end-of-period projection: current tally + Σ(remaining games × expected/g) with a confidence band, for both teams, reading only via the as-of layer
- [x] 2.2 Derive per-category win probability and a safe/contested/gone label (normal approximation first; calibrate later)
- [x] 2.3 Apply variance profiles so equal margins in high-variance cats read less safe than in low-variance cats
- [x] 2.4 Make projection availability-reactive (OUT designations zero remaining contribution and re-open cats)
- [x] 2.5 Test: projection excludes future games; labels sharpen as the period resolves; injury re-opens a category

## 3. Call feed (signals, relevance, opponent, reconciliation)

- [x] 3.1 Signal detection: typed signals (usage/efficiency trend, role/depth-chart change, availability, opponent move) with evidence
- [x] 3.2 Signal strength = confidence × impact-on-contested-cat × relevance; soft↔strong grading (mirage stays soft; sustained+causal+high-impact is strong)
- [x] 3.3 Relevance model: owner class, live matchup categories, season stage; opponent add/drop → inferred targeted/conceded cats
- [x] 3.4 End-of-day reconciliation: summarise the day's relevant signals into candidate add/drop moves per line of play, with projected per-category impact; flag dropping an unplayed player
- [x] 3.5 Test: feed is point-in-time; strength/relevance ordering behaves; a reconciliation move names add/drop/line-of-play + projected impact

## 4. Call-feed log (reframed recommendation-log)

- [x] 4.1 Add signal and reconciliation-move record types (perspective, evidence/strength or add/drop/line-of-play/projected-impact); keep append-only integrity
- [x] 4.2 Reproducible-from-log: as-of + perspective + inputs ref re-run to the same signal/move
- [x] 4.3 Scoring: grade a move by realized category impact vs standing pat (counterfactual over historical box scores); measure projection calibration
- [x] 4.4 Test: append-only holds; a move's realized impact is computed; a "safe" label is calibration-checked

## 5. Baseline retention & glue

- [x] 5.1 Retain the previous ranked engine in code as a labeled replay baseline (do not delete)
- [x] 5.2 CLI: `feed --as-of <date> --league <id> --team <id>` (live signals + end-of-day reconciliation); `project --as-of ... --team ...`
- [x] 5.3 Run end-to-end offline over a simulated league; confirm signals, a reconciliation move, and log rows
- [x] 5.4 `openspec validate call-feed-and-matchup-projection --strict` + test suite + ruff; fix issues

## 6. Assumptions: corrections & validation harness (round 1, D10)

- [x] 6.1 Fix A8: percentage categories (fg_pct/ft_pct) volume-weighted (Σmakes/Σattempts) in tally, projection, and totals — with a binomial SE, not a sum of per-game percentages
- [x] 6.2 `validation/measure.py`: measure per-category coefficient of variation (A1); derive a normalised variance profile
- [x] 6.3 Projector consumes an optional measured variance profile, falling back to the provisional grouping
- [x] 6.4 `bootstrap_category_winprob` (A3): Monte-Carlo win prob to check the normal approximation
- [x] 6.5 `validate` CLI + tests (mechanism on synthetic; real numbers require the nba_api backfill)
- [ ] 6.6 (Blocked on real data) Run the harness on the nba_api backfill; replace provisional constants with measured values; decide whether to drop the variance multiplier (A2)
- [ ] 6.7 (Future) Reliability diagram for safe/contested/gone thresholds (A7); estimator/window study (A5); z-score values (A6)

## 7. Non-code setup (carried from the foundation; parallel, not in specs)

- [ ] 7.1 Create the X account and a waitlist landing page (audience-building starts earliest)
- [ ] 7.2 Create a US-registered Yahoo account and confirm Fantasy Sports appears under API Permissions at developer.yahoo.com/apps/create (unblocks league sync in a later change)
