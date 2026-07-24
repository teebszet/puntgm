# historical-data-pipeline Specification

## Purpose
TBD - created by archiving change bootstrap-historical-data-and-engine. Update Purpose after archive.
## Requirements
### Requirement: Season backfill

The system SHALL backfill a full NBA season of games, box scores, schedules, and player game logs into local storage from a public data source. The primary target is the 2025-26 season; the pipeline SHALL accept a season parameter so additional seasons (2023-24, 2024-25) can be backfilled for the validation set.

#### Scenario: Backfill a completed season

- **WHEN** the backfill is run for the 2025-26 season
- **THEN** the store contains every regular-season game with its date, teams, and final box score
- **AND** each player's per-game log for that season is retrievable

#### Scenario: Backfill an additional prior season

- **WHEN** the backfill is run with a prior season parameter (e.g. 2024-25)
- **THEN** that season's games, box scores, schedules, and player logs are stored alongside the primary season, each tagged with its season

#### Scenario: Backfill is resumable and idempotent

- **WHEN** the backfill is interrupted and re-run
- **THEN** already-fetched data is not duplicated
- **AND** only missing records are fetched

### Requirement: Point-in-time availability data

The system SHALL store player injury and availability status with the date each status became known, so that historical availability can be reconstructed as of any date. Each availability record SHALL carry its `source` and a `confidence` value so that later enrichment from additional sources can be added without altering existing records.

#### Scenario: Availability reflects only what was known

- **WHEN** availability is queried for a player as of date D
- **THEN** the result reflects only injury/availability designations with a known-from date on or before D
- **AND** designations first known after D are excluded

#### Scenario: Availability records carry source and confidence

- **WHEN** an availability designation is stored
- **THEN** the record includes the source it came from and a confidence value
- **AND** records from different sources for the same player and date coexist rather than overwriting one another

#### Scenario: Provenance recorded when dated data is unavailable

- **WHEN** a source cannot provide a dated availability history for a player
- **THEN** the limitation is recorded in the data's provenance
- **AND** the record is not silently backfilled as if known earlier

### Requirement: Point-in-time query guarantee

The data store SHALL expose reads constrained by an as-of date such that no record known only after that date is ever returned.

#### Scenario: As-of query excludes future knowledge

- **WHEN** any dataset is read with an as-of date D
- **THEN** every returned record has a known-from (or event) date on or before D

### Requirement: Point-in-time league state

The system SHALL model fantasy-league state as point-in-time data: for each league, the team rosters, the weekly matchup schedule (which team faces which each scoring period), and the per-category running tally for the active matchup. Each league SHALL carry its settings, including a `lineup_cadence` of weekly-lock or daily-change and its scoring categories.

#### Scenario: League state is reconstructable as of a date

- **WHEN** a league's state is queried as of date D
- **THEN** the result gives each team's roster, the current scoring period's matchup pairing, and the per-category tally as they stood on the morning of D
- **AND** no roster move, matchup, or tally first known after D is reflected

#### Scenario: League records its cadence and categories

- **WHEN** a league is stored
- **THEN** its settings include a `lineup_cadence` (weekly-lock or daily-change) and the set of scoring categories used to decide matchups

### Requirement: Simulated league generation

The system SHALL be able to generate simulated leagues over a backfilled season as the primary source of league state: drafting rosters from an ADP ordering, assigning a weekly matchup schedule, and advancing per-category tallies from the season's box scores. Simulated leagues SHALL be reproducible from a seed.

#### Scenario: A simulated league is reproducible

- **WHEN** a simulated league is generated for a season with a given seed and settings
- **THEN** re-generating with the same seed and settings yields identical rosters and matchup schedule

#### Scenario: Simulated league state advances point-in-time

- **WHEN** a simulated league is queried as of date D
- **THEN** its per-category tallies reflect only games completed on or before D
- **AND** its rosters reflect only moves dated on or before D

### Requirement: Read-only import of the user's own leagues

The system SHALL support a one-time, read-only import of the user's own past Yahoo leagues as a real-world validation set, for the seasons and data Yahoo makes retrievable. Imported leagues SHALL be marked as real (versus simulated) and SHALL record provenance for any state that could not be retrieved point-in-time.

#### Scenario: A real league is imported and marked

- **WHEN** the user's past Yahoo league is imported for a retrievable season
- **THEN** its final rosters, draft results, and weekly matchup results are stored and the league is marked as real
- **AND** where roster-as-of-date history is unavailable, the limitation is recorded in provenance rather than fabricated

#### Scenario: Import performs no write actions

- **WHEN** a league is imported
- **THEN** the operation only reads from Yahoo and never modifies the user's league

