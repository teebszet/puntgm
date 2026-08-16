## ADDED Requirements

### Requirement: Forward-season projections behind a pluggable source

The system SHALL expose forward-season per-player category projections through a source interface
with interchangeable implementations, so the engine depends on the interface rather than on any
particular provider. At least one implementation derived from the project's own data SHALL be
provided, and a fixed-fixture implementation SHALL be available for testing.

#### Scenario: The engine consumes projections through the interface

- **WHEN** the draft engine requests projections for a season
- **THEN** it receives them through the source interface
- **AND** it does not depend on which implementation supplied them

#### Scenario: Sources are interchangeable without engine changes

- **WHEN** the configured projection source is replaced with another implementation
- **THEN** the engine operates unchanged

### Requirement: Projections carry a mean, a production variance, and an uncertainty band

Each projected per-category value SHALL carry an expected value, a measure of game-to-game production
variance, and a separate band expressing uncertainty in the estimate of the mean itself.

#### Scenario: Estimate uncertainty is distinct from production variance

- **WHEN** a player with a long, stable history and a player with a short history have equal projected means
- **THEN** both carry game-to-game production variance
- **AND** the player with the shorter history carries a wider uncertainty band on the mean

#### Scenario: Uncertainty reaches the engine

- **WHEN** the engine values a candidate whose projection has a wide uncertainty band
- **THEN** that uncertainty is reflected in the resulting category win probabilities

### Requirement: Expected games played is projected separately

The system SHALL project the number of games a player is expected to play, as a distinct output from
per-game production, and value SHALL be computed from both rather than from per-game rates alone.

#### Scenario: A high-rate, low-availability player is not overvalued

- **WHEN** two players have equal per-game production but different expected games played
- **THEN** the player expected to play more games receives the higher projected season value

#### Scenario: Availability uncertainty is expressed

- **WHEN** a player's expected games played is uncertain
- **THEN** the projection carries that uncertainty rather than a point estimate alone

### Requirement: Minutes and role drive the projection

The projection SHALL derive per-game production from projected minutes and role rather than
carrying forward prior-season per-game production directly, and SHALL react to depth-chart position
and offseason team changes.

#### Scenario: A team change alters the projection

- **WHEN** a player changes teams into a materially different depth-chart position
- **THEN** the projected minutes change
- **AND** the projected per-game production changes accordingly

#### Scenario: Prior-season rates alone are insufficient

- **WHEN** a player's role changed but their prior-season per-game rates did not
- **THEN** the projection differs from a naive carry-forward of those rates

### Requirement: Players without prior NBA production use an explicit labeled prior

Players with no prior NBA game history SHALL be projected from an explicit prior rather than from
game-log modeling, the prior SHALL be labeled as such in the projection output, and its uncertainty
SHALL reflect the prior's out-of-sample error.

#### Scenario: A rookie projection is labeled

- **WHEN** a player with no NBA games is projected
- **THEN** the projection is marked as prior-derived
- **AND** it carries an uncertainty band reflecting the prior's measured error

#### Scenario: A manual override is recorded as such

- **WHEN** a manual projection override is supplied for a player
- **THEN** the projection records that it was overridden rather than modeled

### Requirement: Projection method is backtestable

The projection method SHALL be runnable against a historical season using only inputs available
before that season, and SHALL report error against that season's realized production and against a
naive prior-season carry-forward baseline.

#### Scenario: Method is scored on a completed season

- **WHEN** the projection method is backtested on a completed season
- **THEN** it reports error on projected minutes and on each category
- **AND** it reports the same error measures for a naive carry-forward baseline

#### Scenario: Backtest uses no information from the projected season

- **WHEN** a historical season is projected for backtesting
- **THEN** only inputs dated before that season's start are used
