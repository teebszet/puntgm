## ADDED Requirements

### Requirement: Forward-season roster and depth-chart inputs

The store SHALL hold, for an upcoming season, each player's team, depth-chart position, and the
offseason transactions that moved them, so that role and minutes can be projected for a season that
has not yet been played.

#### Scenario: A traded player's new team is available before the season

- **WHEN** projections are requested for an upcoming season
- **THEN** each player's current team and depth-chart position are available
- **AND** the offseason transactions that produced them are recorded

#### Scenario: Forward inputs are dated

- **WHEN** forward-season roster inputs are stored
- **THEN** each carries the date it was known
- **AND** reads for an as-of date exclude inputs recorded after it

### Requirement: Entering players without NBA history are represented

The store SHALL represent players entering the league who have no NBA game logs, including their
draft position and team, so they can be projected and drafted.

#### Scenario: An incoming rookie is present in the player pool

- **WHEN** the player pool for an upcoming season is read
- **THEN** players with no prior NBA games are included
- **AND** each carries draft position and team where known

### Requirement: Average draft position

The store SHALL hold average draft position for the upcoming season, keyed to the same player
identifiers as the rest of the store.

#### Scenario: ADP is available for the opponent model

- **WHEN** the draft engine requests average draft position
- **THEN** it is returned keyed to store player identifiers

#### Scenario: Players without ADP are handled explicitly

- **WHEN** a player in the pool has no average draft position
- **THEN** the absence is represented explicitly rather than as a default value
