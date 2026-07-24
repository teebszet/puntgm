## ADDED Requirements

### Requirement: Point-in-time usage and role data

The system SHALL store, effective-dated, each player's usage and role signals — minutes,
shot attempts, starter/bench status, and depth-chart position — so that a role as of any date
can be reconstructed and a usage change can be attributed to a depth-chart cause.

#### Scenario: Usage/role is reconstructable as of a date

- **WHEN** a player's usage/role is queried as of date D
- **THEN** the result reflects minutes, shot attempts, starter/bench, and depth-chart position as known on or before D
- **AND** no usage or role change first known after D is reflected

#### Scenario: A usage change can be attributed

- **WHEN** a player's usage rises across recent games
- **THEN** the stored role data supports identifying whom they moved ahead of on the depth chart and whether a returning player would demote them

### Requirement: Per-player production distributions

The system SHALL expose, for each player as of a date, an expected-per-game value per category
together with a consistency/variance measure computed only from games known on or before that date.

#### Scenario: Expected production carries a confidence band

- **WHEN** a player's expected per-game production is requested as of date D
- **THEN** each category returns a central estimate and a variance/consistency measure
- **AND** both are computed only from games on or before D

### Requirement: Per-category variance profiles

The system SHALL provide per-category variance profiles distinguishing lower-variance categories
(points, rebounds) from higher-variance categories (steals, blocks, assists) for use by projection.

#### Scenario: Variance profile is available to projection

- **WHEN** the projection requests the variance profile for a category
- **THEN** a profile is returned indicating relative variance
- **AND** points and rebounds are marked lower-variance than steals, blocks, and assists
