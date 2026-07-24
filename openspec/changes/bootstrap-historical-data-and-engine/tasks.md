## 1. Project scaffolding

- [x] 1.1 Create Python package layout (`fantasy_gm/` with `data/`, `engine/`, `log/`, `tests/`) and `pyproject.toml` (Python 3.11+)
- [x] 1.2 Set up dependency management (venv + `nba_api`, plus dev deps: `pytest`, `ruff`) and a `.gitignore` covering the local data dir and secrets
- [x] 1.3 Add project `README.md` (what it is, how to run backfill, how to get recommendations) and a `config` module (season(s), scoring-window default = weekly Mon–Sun, `lineup_cadence` per league)

## 2. Historical data pipeline

- [x] 2.1 Implement a raw-response cache-to-disk layer so backfill is resumable and idempotent
- [x] 2.2 Backfill games + schedules for the 2025-26 season into SQLite (parameterize by season; support backfilling 2024-25 / 2023-24 for the validation set)
- [x] 2.3 Backfill per-player game logs / box scores into SQLite
- [x] 2.4 Model availability with effective-dating (`known_from`), capturing dated injury/availability states with `source` + `confidence` fields; record provenance where dated data is unavailable
- [x] 2.5 Implement the as-of read layer (repository) that filters every query by `known_from`/event date <= as_of
- [x] 2.6 Test: an as-of query for date D never returns records known only after D (lookahead guard)

## 3. Decision engine (skeleton)

- [x] 3.1 Define the league-state input (rosters for all teams, wire, settings, active weekly matchup + per-category tally) and a scoring-window abstraction derived from `lineup_cadence`
- [x] 3.2 Accept a perspective (deciding team + opponent) in the engine signature; skeleton may leave matchup/category context unweighted but must not reject it
- [x] 3.3 Implement deterministic candidate scoring = f(games in window, recent per-game production, availability), reading only via the as-of layer
- [x] 3.4 Attach a human-readable reasoning string to every candidate; exclude all rostered players (any team) from candidates
- [x] 3.5 Test: recommendations for date D are unaffected by data dated after D; only wire players are returned; output is scoped to the given perspective

## 4. Recommendation log

- [x] 4.1 Implement append-only structured log (created_at, as_of_date, inputs ref, perspective [league, team, scoring period, opponent], candidate, rank, score, reasoning, confidence)
- [x] 4.2 Wire the engine to write a log record for every recommendation produced
- [x] 4.3 Test: log is append-only and a record's as_of + inputs ref + perspective reproduce the same recommendation

## 5. League state, simulation & real-league import

- [x] 5.1 Model point-in-time league state: rosters (all teams), weekly matchup schedule, per-category running tally, and league settings (`lineup_cadence`, categories); expose via the as-of read layer
- [x] 5.2 Implement seeded simulated-league generation (draft from ADP, snake draft, weekly matchup schedule; tallies advance from box scores) — the primary league-state source
- [x] 5.3 Test: a simulated league is reproducible from its seed; its as-of state reflects only games/moves on or before D
- [x] 5.4 (Secondary) Read-only import of the user's own past Yahoo leagues; mark as real vs simulated; record provenance for state not retrievable point-in-time. Confirm per-season retrievability before committing to the 3-season target

## 6. Milestone glue & verification

- [x] 6.1 Add a CLI/script: `backfill` and `recommend --as-of <date> --league <id> --team <id>` that exercises the full path (perspective-scoped)
- [x] 6.2 Run end-to-end on a sample date + simulated league; sanity-check output and confirm log rows written
- [x] 6.3 Run `openspec validate bootstrap-historical-data-and-engine --strict` and the test suite; fix issues

## 7. Non-code setup (parallel, tracked here — not in specs)

- [ ] 7.1 Create the X account and a waitlist landing page (audience-building starts earliest)
- [ ] 7.2 Create a US-registered Yahoo account and confirm Fantasy Sports appears under API Permissions at developer.yahoo.com/apps/create (unblocks league sync in a later change)
