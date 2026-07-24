## 1. Project scaffolding

- [ ] 1.1 Create Python package layout (`fantasy_gm/` with `data/`, `engine/`, `log/`, `tests/`) and `pyproject.toml` (Python 3.11+)
- [ ] 1.2 Set up dependency management (venv + `nba_api`, plus dev deps: `pytest`, `ruff`) and a `.gitignore` covering the local data dir and secrets
- [ ] 1.3 Add project `README.md` (what it is, how to run backfill, how to get recommendations) and a `config` module (season, scoring-window default = weekly Mon–Sun)

## 2. Historical data pipeline

- [ ] 2.1 Implement a raw-response cache-to-disk layer so backfill is resumable and idempotent
- [ ] 2.2 Backfill games + schedules for the 2025-26 season into SQLite
- [ ] 2.3 Backfill per-player game logs / box scores into SQLite
- [ ] 2.4 Model availability with effective-dating (`known_from`), capturing dated injury/availability states; record provenance where dated data is unavailable
- [ ] 2.5 Implement the as-of read layer (repository) that filters every query by `known_from`/event date <= as_of
- [ ] 2.6 Test: an as-of query for date D never returns records known only after D (lookahead guard)

## 3. Decision engine (skeleton)

- [ ] 3.1 Define the league-state input (rostered players, wire, settings) and a scoring-window abstraction
- [ ] 3.2 Implement deterministic candidate scoring = f(games in window, recent per-game production, availability), reading only via the as-of layer
- [ ] 3.3 Attach a human-readable reasoning string to every candidate; exclude rostered players from candidates
- [ ] 3.4 Test: recommendations for date D are unaffected by data dated after D; only wire players are returned

## 4. Recommendation log

- [ ] 4.1 Implement append-only structured log (created_at, as_of_date, inputs ref, candidate, rank, score, reasoning, confidence)
- [ ] 4.2 Wire the engine to write a log record for every recommendation produced
- [ ] 4.3 Test: log is append-only and a record's as_of + inputs ref reproduce the same recommendation

## 5. Milestone glue & verification

- [ ] 5.1 Add a CLI/script: `backfill` and `recommend --as-of <date> --league <file>` that exercises the full path
- [ ] 5.2 Run end-to-end on a sample date from last season; sanity-check output and confirm log rows written
- [ ] 5.3 Run `openspec validate bootstrap-historical-data-and-engine --strict` and the test suite; fix issues

## 6. Non-code setup (parallel, tracked here — not in specs)

- [ ] 6.1 Create the X account and a waitlist landing page (audience-building starts earliest)
- [ ] 6.2 Create a US-registered Yahoo account and confirm Fantasy Sports appears under API Permissions at developer.yahoo.com/apps/create (unblocks league sync in a later change)
