## Why

The product's differentiation and its entire distribution strategy both rest on one thing: a **verifiable track record** produced before the NBA season starts. To generate that, we need last season's NBA data captured *point-in-time* (no lookahead) plus a decision engine whose calls can be scored. Building this first — ahead of any UI or Yahoo league sync — lets the replay harness (a later change) publish evidence in August, months before opening night.

## What Changes

- Add a **historical data pipeline** that backfills the full 2025-26 NBA season (games, box scores, schedules, player game logs, and dated injury/availability status) into local storage.
- Store data as **point-in-time snapshots**: every record is keyed by "as-known-on date D" so downstream consumers can reconstruct exactly what a manager knew on any morning, avoiding lookahead bias.
- Add a **skeleton decision engine** that, given a league state and a date, ranks streaming/waiver candidates for the week ahead using simple, explainable signals (schedule volume, recent per-game production, availability).
- Add a **recommendation log**: every engine call writes a structured record (timestamp, as-of date, inputs, the call, reasoning, confidence). This log is the shared source of truth for the future eval suite and marketing content.
- Establish project scaffolding: Python package layout, dependency management, config, and tests.

Non-code setup tracked in tasks but out of scope for specs: creating the X account + waitlist landing page, and resolving Yahoo API access via a US-registered account.

## Capabilities

### New Capabilities

- `historical-data-pipeline`: Backfill and store point-in-time NBA data (games, box scores, schedules, player logs, injury/availability) for a season.
- `decision-engine`: Given a point-in-time league state and date, produce ranked, explainable streaming/waiver recommendations for a scoring window.
- `recommendation-log`: Persist every recommendation with full context (as-of date, inputs, output, reasoning, confidence) as the source of truth for evals and content.

### Modified Capabilities

<!-- None — greenfield project. -->

## Impact

- New Python project (`fantasy-nba-gm`): package `fantasy_gm` with `data/`, `engine/`, `log/` modules.
- New dependency: `nba_api` (free NBA.com endpoints) for backfill; `balldontlie` deferred to a later change as the licensed live source.
- Local data store (SQLite or parquet + DuckDB) for point-in-time snapshots and the recommendation log.
- No external services, no OAuth, no network write actions in this milestone.
