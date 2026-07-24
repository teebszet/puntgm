## 1. Data extensions (point-in-time)

- [ ] 1.1 Store effective-dated usage/role: minutes, shot attempts, starter/bench, depth-chart position; expose via the as-of layer
- [ ] 1.2 Expose per-player expected-per-game per category with a variance/consistency measure, as of a date
- [ ] 1.3 Provide per-category variance profiles (pts/reb lower-variance; stl/blk/ast higher-variance)
- [ ] 1.4 Extend the synthetic season to emit usage/role trends and variance so the above are exercisable offline

## 2. Matchup projection (the spine)

- [ ] 2.1 Implement per-category end-of-period projection: current tally + Σ(remaining games × expected/g) with a confidence band, for both teams, reading only via the as-of layer
- [ ] 2.2 Derive per-category win probability and a safe/contested/gone label (normal approximation first; calibrate later)
- [ ] 2.3 Apply variance profiles so equal margins in high-variance cats read less safe than in low-variance cats
- [ ] 2.4 Make projection availability-reactive (OUT designations zero remaining contribution and re-open cats)
- [ ] 2.5 Test: projection excludes future games; labels sharpen as the period resolves; injury re-opens a category

## 3. Call feed (signals, relevance, opponent, reconciliation)

- [ ] 3.1 Signal detection: typed signals (usage/efficiency trend, role/depth-chart change, availability, opponent move) with evidence
- [ ] 3.2 Signal strength = confidence × impact-on-contested-cat × relevance; soft↔strong grading (mirage stays soft; sustained+causal+high-impact is strong)
- [ ] 3.3 Relevance model: owner class, live matchup categories, season stage; opponent add/drop → inferred targeted/conceded cats
- [ ] 3.4 End-of-day reconciliation: summarise the day's relevant signals into candidate add/drop moves per line of play, with projected per-category impact; flag dropping an unplayed player
- [ ] 3.5 Test: feed is point-in-time; strength/relevance ordering behaves; a reconciliation move names add/drop/line-of-play + projected impact

## 4. Call-feed log (reframed recommendation-log)

- [ ] 4.1 Add signal and reconciliation-move record types (perspective, evidence/strength or add/drop/line-of-play/projected-impact); keep append-only integrity
- [ ] 4.2 Reproducible-from-log: as-of + perspective + inputs ref re-run to the same signal/move
- [ ] 4.3 Scoring: grade a move by realized category impact vs standing pat (counterfactual over historical box scores); measure projection calibration
- [ ] 4.4 Test: append-only holds; a move's realized impact is computed; a "safe" label is calibration-checked

## 5. Baseline retention & glue

- [ ] 5.1 Retain the previous ranked engine in code as a labeled replay baseline (do not delete)
- [ ] 5.2 CLI: `feed --as-of <date> --league <id> --team <id>` (live signals + end-of-day reconciliation); `project --as-of ... --team ...`
- [ ] 5.3 Run end-to-end offline over a simulated league; confirm signals, a reconciliation move, and log rows
- [ ] 5.4 `openspec validate call-feed-and-matchup-projection --strict` + test suite + ruff; fix issues

## 6. Non-code setup (carried from the foundation; parallel, not in specs)

- [ ] 6.1 Create the X account and a waitlist landing page (audience-building starts earliest)
- [ ] 6.2 Create a US-registered Yahoo account and confirm Fantasy Sports appears under API Permissions at developer.yahoo.com/apps/create (unblocks league sync in a later change)
