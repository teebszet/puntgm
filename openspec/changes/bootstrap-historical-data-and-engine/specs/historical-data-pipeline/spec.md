## ADDED Requirements

### Requirement: Season backfill

The system SHALL backfill a full NBA season of games, box scores, schedules, and player game logs into local storage from a public data source.

#### Scenario: Backfill a completed season

- **WHEN** the backfill is run for the 2025-26 season
- **THEN** the store contains every regular-season game with its date, teams, and final box score
- **AND** each player's per-game log for that season is retrievable

#### Scenario: Backfill is resumable and idempotent

- **WHEN** the backfill is interrupted and re-run
- **THEN** already-fetched data is not duplicated
- **AND** only missing records are fetched

### Requirement: Point-in-time availability data

The system SHALL store player injury and availability status with the date each status became known, so that historical availability can be reconstructed as of any date.

#### Scenario: Availability reflects only what was known

- **WHEN** availability is queried for a player as of date D
- **THEN** the result reflects only injury/availability designations with a known-from date on or before D
- **AND** designations first known after D are excluded

#### Scenario: Provenance recorded when dated data is unavailable

- **WHEN** a source cannot provide a dated availability history for a player
- **THEN** the limitation is recorded in the data's provenance
- **AND** the record is not silently backfilled as if known earlier

### Requirement: Point-in-time query guarantee

The data store SHALL expose reads constrained by an as-of date such that no record known only after that date is ever returned.

#### Scenario: As-of query excludes future knowledge

- **WHEN** any dataset is read with an as-of date D
- **THEN** every returned record has a known-from (or event) date on or before D
